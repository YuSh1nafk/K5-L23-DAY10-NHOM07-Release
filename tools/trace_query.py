#!/usr/bin/env python3
"""Pha 5: truy vết ticket của team AI -> trace.json.

  python tools/trace_query.py runs/r7007/delivery --out runs/r7007/trace.json
  python tools/trace_query.py delivery --out submission/de_an/trace.json          # đề ẩn

Đọc incoming/ticket.md (cảnh bị báo, lớp bị báo) và guideline/changelog.md (lớp đổi quy tắc,
phiên bản cũ/mới). Có thể ghi đè bằng --scenes / --cls / --old nếu ticket viết khác mẫu.

  tier_a : ảnh trong các cảnh team AI nêu, thuộc job theo guideline CŨ (jobs.csv),
           có >= 1 box detection lớp bị đổi quy tắc (labels/ của lô)
  tier_b : ảnh khác cùng điều kiện (job guideline cũ, có box lớp đó) mà chưa ai báo
  notify : mã người gán + người kiểm của các job chứa ảnh tầng a và b

Guideline lấy theo JOB trong jobs.csv, không theo metadata từng ảnh (có thể chính là lỗi guideline_mismatch).
Chỉ thư viện chuẩn.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


def versions(text: str) -> list[str]:
    found = re.findall(r"v(\d+)\.(\d+)", text)
    uniq = sorted({(int(a), int(b)) for a, b in found})
    return [f"v{a}.{b}" for a, b in uniq]


def parse_changelog(text: str, class_names: set[str]) -> tuple[str | None, str | None]:
    """Trả (lớp bị đổi quy tắc, phiên bản mới ban hành quy tắc đó)."""
    sections = re.split(r"^##\s+", text, flags=re.M)[1:]
    for sec in sections:
        head, _, body = sec.partition("\n")
        for cls in re.findall(r"`([^`]+)`", body):
            if cls in class_names:
                v = versions(head)
                return cls, (v[-1] if v else None)
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Truy vết ticket -> trace.json")
    ap.add_argument("delivery")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scenes", nargs="*", help="ghi đè danh sách cảnh team AI nêu")
    ap.add_argument("--cls", help="ghi đè tên lớp bị đổi quy tắc")
    ap.add_argument("--old", nargs="*", help="ghi đè các phiên bản guideline cũ")
    args = ap.parse_args()

    d = Path(args.delivery)
    classes = {}
    for ln in (d / "classes.txt").read_text(encoding="utf-8").splitlines():
        k, _, v = ln.strip().partition(" ")
        if k.isdigit():
            classes[int(k)] = v.strip()
    with (d / "jobs.csv").open(encoding="utf-8", newline="") as f:
        jobs = {r["job_id"].strip(): r for r in csv.DictReader(f)}
    meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))["items"]
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["items"]

    ticket = (d / "incoming" / "ticket.md").read_text(encoding="utf-8")
    changelog = (d / "guideline" / "changelog.md").read_text(encoding="utf-8")

    cls, new_version = parse_changelog(changelog, set(classes.values()))
    cls = args.cls or cls
    scenes = args.scenes or sorted(set(re.findall(r"scene_\w+", ticket)))
    all_versions = sorted({j["guideline"].strip() for j in jobs.values()}, key=lambda v: [int(x) for x in v[1:].split(".")])
    if args.old:
        old = set(args.old)
    elif new_version:
        old = {v for v in all_versions if [int(x) for x in v[1:].split(".")] < [int(x) for x in new_version[1:].split(".")]}
    else:
        old = set(all_versions[:1])
    if not cls or not scenes or not old:
        raise SystemExit(f"không đọc được ticket/changelog (lớp={cls}, cảnh={scenes}, cũ={old}); dùng --cls/--scenes/--old")
    cls_ids = {i for i, n in classes.items() if n == cls}

    def has_cls(name: str) -> bool:
        rel = man.get(name, {}).get("label", f"labels/{name}.txt")
        path = d / rel
        if not path.is_file():
            return False
        for ln in path.read_text(encoding="utf-8").splitlines():
            parts = ln.split()
            if parts and parts[0].lstrip("-").isdigit() and int(parts[0]) in cls_ids:
                return True
        return False

    tier_a, tier_b, notify = [], [], set()
    for m in meta:
        job = jobs.get(str(m.get("job_id", "")).strip())
        if not job or job["guideline"].strip() not in old or not has_cls(m["image"]):
            continue
        (tier_a if m["scene_id"] in scenes else tier_b).append(m["image"])
        notify.update([job["annotator"].strip(), job["reviewer"].strip()])

    out = {"tier_a": sorted(tier_a), "tier_b": sorted(tier_b), "notify": sorted(notify)}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"lớp đổi quy tắc: {cls} (từ {new_version}); guideline cũ: {sorted(old)}; cảnh được báo: {scenes}")
    print(f"tier_a {len(tier_a)} ảnh, tier_b {len(tier_b)} ảnh, notify {sorted(notify)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
