#!/usr/bin/env python3
"""Validator lỗi luật cho lô bàn giao Day 10.

    python validator/validate.py <thư_mục_delivery> --out <errors.json> [--candidates <candidates.json>]

Chỉ đọc thư mục delivery được truyền vào, chỉ dùng thư viện chuẩn (+ scripts/pngutil.py), không mạng.

Lỗi luật bắt được (Pha 1):
  file       : missing_image, wrong_hash
  detection  : invalid_class, bbox_out_of_bounds, duplicate_box
  metadata   : scene_leak (target = scene_id), guideline_mismatch (so với guideline của job)
  panoptic   : invalid_category_id, thing_overlap

--candidates: phần đối chiếu detection <-> panoptic là việc của Pha 2, hiện chỉ ghi danh sách rỗng.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pngutil  # noqa: E402  (đọc PNG, thư viện chuẩn)

# ---------------------------------------------------------------- ngưỡng
# bbox_out_of_bounds: cho phép sai số làm tròn tối đa nửa pixel khi box chạm mép ảnh.
OOB_TOLERANCE_PX = 0.5
# duplicate_box: box lặp có cùng kích thước (sai khác <= 1 px do làm tròn 6 chữ số)
# và tâm chỉ lệch vài pixel. Cặp box tự nhiên chồng nhau thường khác kích thước.
DUP_SIZE_TOL_PX = 1.0
DUP_CENTER_TOL_PX = 3.0
# thing_overlap: `area` khai trong panoptic.json lớn hơn số pixel thật của segment.
# Với COCO gốc hai số này bằng nhau; cho phép lệch nhỏ để an toàn.
OVERLAP_MIN_RATIO = 0.03   # thiếu >= 3% diện tích khai báo
OVERLAP_MIN_PX = 10        # và thiếu >= 10 pixel


# ---------------------------------------------------------------- tiện ích đọc file
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:  # tính trên bytes, không đọc dạng text
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_classes(path: Path) -> dict[int, str]:
    classes = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[0].lstrip("-").isdigit():
            classes[int(parts[0])] = parts[1]
    return classes


def read_jobs(path: Path) -> dict[str, dict]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return {row["job_id"].strip(): row for row in csv.DictReader(f)}


def read_yolo(path: Path) -> list[list[str]]:
    return [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def jpeg_size(path: Path) -> tuple[int, int] | None:
    """Đọc (width, height) từ header JPEG (marker SOFn), không cần Pillow."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01, 0xFF) or 0xD0 <= marker <= 0xD7:
            i += 1 if marker == 0xFF else 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    return None


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            return struct.unpack(">II", head[16:24])
    except OSError:
        pass
    return None


# ---------------------------------------------------------------- validator
class Validator:
    def __init__(self, delivery: Path):
        self.d = delivery
        self.errors: list[dict] = []
        self._seen: set[tuple[str, str]] = set()

        self.manifest = load_json(delivery / "manifest.json")["items"]
        self.metadata = {it["image"]: it for it in load_json(delivery / "metadata.json")["items"]}
        self.jobs = read_jobs(delivery / "jobs.csv")
        self.classes = read_classes(delivery / "classes.txt")
        self.panoptic = load_json(delivery / "panoptic.json")

        # kích thước ảnh: ưu tiên panoptic.json, sau đó header JPEG, cuối cùng header PNG của mask
        self.pan_images = {Path(im["file_name"]).stem: im for im in self.panoptic.get("images", [])}
        self._size_cache: dict[str, tuple[int, int] | None] = {}

    def add(self, kind: str, target: str) -> None:
        key = (kind, target)
        if key not in self._seen:
            self._seen.add(key)
            self.errors.append({"type": kind, "target": target})

    def image_size(self, name: str) -> tuple[int, int] | None:
        if name not in self._size_cache:
            size = None
            im = self.pan_images.get(name)
            if im and im.get("width") and im.get("height"):
                size = (int(im["width"]), int(im["height"]))
            if size is None:
                entry = self.manifest.get(name, {})
                if entry.get("image"):
                    size = jpeg_size(self.d / entry["image"])
                if size is None and entry.get("mask"):
                    size = png_size(self.d / entry["mask"])
            self._size_cache[name] = size
        return self._size_cache[name]

    # ------------------------------------------------------------ file
    def check_files(self) -> None:
        for name, entry in self.manifest.items():
            image_rel = entry.get("image")
            image_path = self.d / image_rel if image_rel else None
            if image_path is None or not image_path.is_file():
                self.add("missing_image", name)
                continue
            for field, hash_field in (("image", "image_sha256"), ("label", "label_sha256"), ("mask", "mask_sha256")):
                rel, expected = entry.get(field), entry.get(hash_field)
                if not rel or not expected:
                    continue
                path = self.d / rel
                if path.is_file() and sha256_file(path) != expected.strip().lower():
                    self.add("wrong_hash", name)
                    break

    # ------------------------------------------------------------ detection
    def check_labels(self) -> None:
        num_classes = len(self.classes) or 80
        for name, entry in self.manifest.items():
            rel = entry.get("label")
            if not rel or not (self.d / rel).is_file():
                continue
            rows = read_yolo(self.d / rel)
            size = self.image_size(name)

            boxes = []  # (class_id, cx, cy, w, h) theo pixel
            bad_class = out_of_bounds = False
            for row in rows:
                if len(row) != 5:
                    bad_class = True
                    continue
                try:
                    cls = int(row[0])
                    x, y, w, h = map(float, row[1:5])
                except ValueError:
                    bad_class = True
                    continue
                if not 0 <= cls < num_classes or cls not in self.classes:
                    bad_class = True
                if size:
                    W, H = size
                    tol_x, tol_y = OOB_TOLERANCE_PX / W, OOB_TOLERANCE_PX / H
                else:
                    W = H = 1
                    tol_x = tol_y = 1e-3
                if (x - w / 2 < -tol_x or x + w / 2 > 1 + tol_x or
                        y - h / 2 < -tol_y or y + h / 2 > 1 + tol_y or w <= 0 or h <= 0):
                    out_of_bounds = True
                boxes.append((cls, x * W, y * H, w * W, h * H))

            if bad_class:
                self.add("invalid_class", name)
            if out_of_bounds:
                self.add("bbox_out_of_bounds", name)
            if size and self._has_duplicate(boxes):
                self.add("duplicate_box", name)

    @staticmethod
    def _has_duplicate(boxes: list[tuple]) -> bool:
        by_class = defaultdict(list)
        for b in boxes:
            by_class[b[0]].append(b)
        for group in by_class.values():
            for i in range(len(group)):
                _, xa, ya, wa, ha = group[i]
                for j in range(i + 1, len(group)):
                    _, xb, yb, wb, hb = group[j]
                    if (abs(wa - wb) <= DUP_SIZE_TOL_PX and abs(ha - hb) <= DUP_SIZE_TOL_PX and
                            abs(xa - xb) <= DUP_CENTER_TOL_PX and abs(ya - yb) <= DUP_CENTER_TOL_PX):
                        return True
        return False

    # ------------------------------------------------------------ metadata
    def check_metadata(self) -> None:
        splits_by_scene: dict[str, set[str]] = defaultdict(set)
        for name, meta in self.metadata.items():
            scene, split = meta.get("scene_id"), meta.get("split")
            if scene and split:
                splits_by_scene[scene].add(split.strip().lower())

            job = self.jobs.get(str(meta.get("job_id", "")).strip())
            image_gl = str(meta.get("guideline_version", "")).strip()
            if job is not None and image_gl and image_gl != job.get("guideline", "").strip():
                self.add("guideline_mismatch", name)

        for scene, splits in sorted(splits_by_scene.items()):
            if len(splits) > 1:
                self.add("scene_leak", scene)

    # ------------------------------------------------------------ panoptic
    def check_panoptic(self) -> None:
        valid_ids = {c["id"] for c in self.panoptic.get("categories", [])}
        for ann in self.panoptic.get("annotations", []):
            name = Path(ann.get("file_name", "")).stem
            segments = ann.get("segments_info", [])
            if any(s.get("category_id") not in valid_ids for s in segments):
                self.add("invalid_category_id", name)

            mask_path = self._mask_path(name, ann)
            if mask_path is None or not segments:
                continue
            counts = self._count_pixels(mask_path)
            for s in segments:
                declared = int(s.get("area", 0))
                actual = counts.get(int(s["id"]), 0)
                missing = declared - actual
                if missing >= OVERLAP_MIN_PX and missing >= OVERLAP_MIN_RATIO * declared:
                    self.add("thing_overlap", name)
                    break

    def _mask_path(self, name: str, ann: dict) -> Path | None:
        entry = self.manifest.get(name, {})
        candidates = [entry.get("mask"), f"masks/{ann.get('file_name', '')}"]
        for rel in candidates:
            if rel and (self.d / rel).is_file():
                return self.d / rel
        return None

    @staticmethod
    def _count_pixels(path: Path) -> Counter:
        _, _, pixels = pngutil.read_rgb(path)
        raw = bytes(pixels)
        rgb = Counter(zip(raw[0::3], raw[1::3], raw[2::3]))
        counts: Counter = Counter()
        for (r, g, b), n in rgb.items():
            sid = r + g * 256 + b * 256 * 256
            if sid:  # id 0 là nền
                counts[sid] += n
        return counts

    # ------------------------------------------------------------ chạy
    def run(self) -> list[dict]:
        self.check_files()
        self.check_labels()
        self.check_metadata()
        self.check_panoptic()
        return self.errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Validator lỗi luật cho lô bàn giao Day 10")
    ap.add_argument("delivery", help="thư mục delivery/ của lô")
    ap.add_argument("--out", required=True, help="file errors.json đầu ra")
    ap.add_argument("--candidates", help="file candidates.json (Pha 2, hiện ghi rỗng)")
    args = ap.parse_args()

    delivery = Path(args.delivery)
    if not (delivery / "manifest.json").is_file():
        ap.error(f"không thấy manifest.json trong {delivery}")

    errors = Validator(delivery).run()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"errors": errors}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.candidates:
        cand = Path(args.candidates)
        cand.parent.mkdir(parents=True, exist_ok=True)
        cand.write_text(json.dumps({"candidates": []}, indent=2) + "\n", encoding="utf-8")

    summary = Counter(e["type"] for e in errors)
    print(f"{len(errors)} lỗi -> {out}")
    for kind, n in sorted(summary.items()):
        print(f"  {kind}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
