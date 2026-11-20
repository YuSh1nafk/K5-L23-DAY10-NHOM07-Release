#!/usr/bin/env python3
"""Pha 4: dựng lô v2 (sản phẩm detection) từ v1 + errors.json, KHÔNG sửa delivery/.

  python tools/build_handover.py --run runs/r7007 [--keep-hash 000000016010 ...]

Đọc   : runs/r<seed>/delivery/ (v1), runs/r<seed>/errors.json (đã gộp review)
Ghi   : runs/r<seed>/handover/<release_id>_v2/
          images/, labels/       ảnh + nhãn giữ nguyên byte của v1, trừ ảnh phải gán lại
          rework_queue.csv       ảnh tách ra, giao đúng annotator/reviewer theo jobs.csv
          manifest.csv           file,sha256 cho mọi file trong images/ và labels/
          label_summary.csv      class,count theo tên lớp, dòng cuối TOTAL
          truyvet.csv            image,scene_id,split,job_id,annotator,reviewer,guideline (guideline theo JOB)
          README.txt             theo templates/README.txt

Quy tắc (GUIDE Pha 4):
  sửa ngay trong v2 : wrong_hash (ảnh còn nguyên -> ghi hash thật), guideline_mismatch (lấy theo job),
                      scene_leak (đưa cả cảnh về split chiếm đa số)
  rework, rời v2    : missing_image, invalid_class, bbox_out_of_bounds, duplicate_box,
                      class_swap, bbox_drift, missing_box; và wrong_hash nếu ảnh hỏng
  ở lại v2          : lỗi chỉ ở panoptic (invalid_category_id, thing_overlap, category_swap, instance_merge)

Ảnh wrong_hash được giữ lại khi giải mã được (Pillow nếu có, không thì kiểm marker JPEG).
Người trong nhóm vẫn phải MỞ ẢNH xem nội dung đúng; dùng --rework-hash <ảnh> để ép đưa vào rework.
Sau khi chạy: python scripts/pack_release.py <thư mục v2>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

DET_REWORK = {"missing_image", "invalid_class", "bbox_out_of_bounds", "duplicate_box",
              "class_swap", "bbox_drift", "missing_box"}
PANOPTIC_ONLY = {"invalid_category_id", "thing_overlap", "category_swap", "instance_merge"}
FIX_IN_V2 = {"wrong_hash", "guideline_mismatch", "scene_leak"}
THRESHOLD = 0.02

ACTION = {
    "missing_image": "giao lại file ảnh gốc rồi kiểm nhãn",
    "invalid_class": "gán lại lớp của box có class_id ngoài 0-79",
    "bbox_out_of_bounds": "vẽ lại box đang tràn ra ngoài mép ảnh",
    "duplicate_box": "xoá box lặp (cùng lớp, cùng kích thước, lệch 1-2 px)",
    "class_swap": "gán lại lớp đúng cho box bị nhầm lớp",
    "bbox_drift": "vẽ lại box bị lệch khỏi vật",
    "missing_box": "vẽ thêm box cho vật bị bỏ sót",
    "wrong_hash": "ảnh hỏng: giao lại file ảnh gốc",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def image_ok(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
        return True
    except ImportError:
        data = path.read_bytes()
        return data[:2] == b"\xff\xd8" and data.rstrip(b"\x00")[-2:] == b"\xff\xd9"
    except Exception:
        return False


def job_ranges(jobs: list[dict]) -> str:
    by_gl = defaultdict(list)
    for j in jobs:
        by_gl[j["guideline"]].append(j["job_id"])
    parts = []
    for gl in sorted(by_gl):
        ids = sorted(by_gl[gl])
        parts.append(f"{gl} ({ids[0]}-{ids[-1]})" if len(ids) > 1 else f"{gl} ({ids[0]})")
    return ", ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Dựng lô v2 không ghi đè v1")
    ap.add_argument("--run", required=True, help="runs/r<seed>")
    ap.add_argument("--rework-hash", nargs="*", default=[], help="ảnh wrong_hash đã mở xem và thấy hỏng -> rework")
    args = ap.parse_args()

    run = Path(args.run)
    d = run / "delivery"
    rid = json.loads((d / "release.json").read_text(encoding="utf-8"))["release_id"]
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["items"]
    meta = {m["image"]: m for m in json.loads((d / "metadata.json").read_text(encoding="utf-8"))["items"]}
    jobs_list = read_csv(d / "jobs.csv")
    jobs = {j["job_id"]: j for j in jobs_list}
    names = {}
    for ln in (d / "classes.txt").read_text(encoding="utf-8").splitlines():
        k, _, v = ln.strip().partition(" ")
        if k.isdigit():
            names[int(k)] = v.strip()
    errors = json.loads((run / "errors.json").read_text(encoding="utf-8"))["errors"]

    by_image = {e["target"]: e["type"] for e in errors if e["target"] in man}
    leak_scenes = sorted({e["target"] for e in errors if e["type"] == "scene_leak"})

    # --- quyết định từng ảnh
    rework, hash_fixed, hash_broken, panoptic_only, gl_fixed = [], [], [], [], []
    for name, kind in sorted(by_image.items()):
        if kind in DET_REWORK:
            rework.append((name, kind))
        elif kind == "wrong_hash":
            img = d / man[name]["image"]
            if name in args.rework_hash or not image_ok(img):
                rework.append((name, kind))
                hash_broken.append(name)
            else:
                hash_fixed.append(name)
        elif kind == "guideline_mismatch":
            gl_fixed.append(name)
        elif kind in PANOPTIC_ONLY:
            panoptic_only.append((name, kind))
    rework_set = {n for n, _ in rework}
    keep = [n for n in sorted(man) if n not in rework_set]

    # --- scene_leak: cả cảnh về split chiếm đa số
    split_fix = {}
    for sc in leak_scenes:
        members = [m for m in meta.values() if m["scene_id"] == sc]
        votes = Counter(m["split"] for m in members)
        top = max(votes.items(), key=lambda kv: (kv[1], kv[0] == "train"))[0]
        moved = sorted(m["image"] for m in members if m["split"] != top)
        split_fix[sc] = (top, moved)

    def split_of(name: str) -> str:
        sc = meta[name]["scene_id"]
        return split_fix[sc][0] if sc in split_fix else meta[name]["split"]

    # --- ghi thư mục v2 (xoá bản cũ của chính v2 nếu chạy lại; KHÔNG đụng delivery/)
    v2 = run / "handover" / f"{rid}_v2"
    if v2.exists():
        shutil.rmtree(v2)
    (v2 / "images").mkdir(parents=True)
    (v2 / "labels").mkdir()
    for n in keep:
        shutil.copyfile(d / man[n]["image"], v2 / "images" / Path(man[n]["image"]).name)
        shutil.copyfile(d / man[n]["label"], v2 / "labels" / Path(man[n]["label"]).name)

    write_csv(v2 / "rework_queue.csv", ["image", "type", "annotator", "reviewer", "action"],
              [[n, k, jobs[meta[n]["job_id"]]["annotator"], jobs[meta[n]["job_id"]]["reviewer"], ACTION.get(k, "gán lại")]
               for n, k in rework])

    files = sorted(p for sub in ("images", "labels") for p in (v2 / sub).iterdir() if p.is_file())
    write_csv(v2 / "manifest.csv", ["file", "sha256"], [[p.relative_to(v2).as_posix(), sha(p)] for p in files])

    counts = Counter()
    for p in sorted((v2 / "labels").glob("*.txt")):
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                c = int(float(ln.split()[0]))
                counts[names.get(c, str(c))] += 1
    order = [names[i] for i in sorted(names) if names[i] in counts]
    write_csv(v2 / "label_summary.csv", ["class", "count"],
              [[c, counts[c]] for c in order] + [["TOTAL", sum(counts.values())]])

    write_csv(v2 / "truyvet.csv", ["image", "scene_id", "split", "job_id", "annotator", "reviewer", "guideline"],
              [[n, meta[n]["scene_id"], split_of(n), meta[n]["job_id"], jobs[meta[n]["job_id"]]["annotator"],
                jobs[meta[n]["job_id"]]["reviewer"], jobs[meta[n]["job_id"]]["guideline"]] for n in keep])

    # --- README.txt
    err_units = {e["target"] for e in errors}
    rate = len(err_units) / len(man)
    v1_decision = "Hold" if rate > THRESHOLD else "Release"
    n_labels = sum(counts.values())
    by_type = Counter(e["type"] for e in errors)
    rework_people = defaultdict(list)
    for n, _ in rework:
        j = jobs[meta[n]["job_id"]]
        rework_people[f"{j['annotator']}/{j['reviewer']}"].append(n)

    notes = []
    if hash_fixed:
        notes.append(f"wrong_hash {', '.join(hash_fixed)}: đã mở ảnh, giải mã được và đúng nội dung so với nhãn, "
                     "chỉ manifest v1 ghi sai mã; v2 giữ ảnh và ghi sha256 thật trong manifest.csv")
    if hash_broken:
        notes.append(f"wrong_hash {', '.join(hash_broken)}: ảnh hỏng, đưa vào rework")
    if gl_fixed:
        notes.append(f"guideline_mismatch {', '.join(gl_fixed)}: metadata ảnh ghi sai phiên bản; truyvet.csv lấy guideline theo job trong jobs.csv")
    for sc, (top, moved) in split_fix.items():
        notes.append(f"scene_leak {sc}: cảnh nằm ở cả train và val; đưa cả cảnh về {top} (chuyển {', '.join(moved)}) để không rò train/val")
    if rework:
        notes.append("rework (rời v2, giao lại đúng người theo jobs.csv): "
                     + "; ".join(f"{who}: {', '.join(ns)}" for who, ns in sorted(rework_people.items())))
    if panoptic_only:
        notes.append("lỗi chỉ ở panoptic, nhãn detection vẫn đúng nên ảnh ở lại v2, báo team panoptic: "
                     + ", ".join(f"{n} ({k})" for n, k in panoptic_only))
    notes.append("nhãn detection trong v2 giữ nguyên byte của v1, không sửa tay")

    readme = [
        f"Release: {rid}_v2",
        f"Parent: {rid}",
        f"Số ảnh: {len(keep)}",
        f"Số nhãn: {n_labels}",
        f"Guideline: {job_ranges(jobs_list)}",
        f"QC v1: {len(err_units)}/{len(man)} = {rate:.1%}".replace(".", ","),
        f"Kết luận v1: {v1_decision} — {len(err_units)} ảnh/cảnh lỗi trên {len(man)} ảnh = {rate:.1%} "
        f"{'vượt' if rate > THRESHOLD else 'không vượt'} ngưỡng 2%; lỗi theo loại: "
        + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items())),
        f"Kết luận v2: Release — sản phẩm detection {len(keep)} ảnh: đã tách {len(rework)} ảnh lỗi nhãn detection/thiếu file "
        f"sang rework, sửa hash/guideline/split ngay trong v2; còn {len(panoptic_only)} ảnh lỗi panoptic (không ảnh hưởng detection) "
        "chờ team panoptic sửa",
        "Ghi chú: " + " | ".join(notes),
    ]
    (v2 / "README.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")

    print(f"v2: {v2}")
    print(f"  giữ {len(keep)} ảnh, {n_labels} nhãn; rework {len(rework)}; panoptic ở lại {len(panoptic_only)}")
    print(f"  QC v1 {len(err_units)}/{len(man)} = {rate:.1%} -> {v1_decision}")
    print(f"Tiếp: python scripts/pack_release.py {v2.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
