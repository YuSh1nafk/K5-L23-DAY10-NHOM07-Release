#!/usr/bin/env python3
"""Validator lỗi luật cho lô bàn giao Day 10.

    python validator/validate.py <thư_mục_delivery> --out <errors.json> [--candidates <candidates.json>]

Chỉ đọc thư mục delivery được truyền vào, chỉ dùng thư viện chuẩn (+ scripts/pngutil.py), không mạng.

Lỗi luật bắt được (Pha 1):
  file       : missing_image, wrong_hash
  detection  : invalid_class, bbox_out_of_bounds, duplicate_box
  metadata   : scene_leak (target = scene_id), guideline_mismatch (so với guideline của job)
  panoptic   : invalid_category_id, thing_overlap

--candidates (Pha 2): đối chiếu detection <-> panoptic của cùng ảnh, ghi ứng viên lỗi gán nhãn
  swap            box và segment trùng vị trí nhưng khác lớp -> class_swap hoặc category_swap (xem ảnh)
  bbox_drift      nhiều pixel của segment cùng lớp rơi ra ngoài box đã ghép
  missing_box     segment thing đáng kể mà không box nào phủ
  instance_merge  một segment trải trên hai box cùng lớp, không box nào chứa trọn
Ứng viên KHÔNG ghi vào errors.json: người trong nhóm phải xem ảnh rồi ghi review_log.csv.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import struct
import sys
from collections import Counter, defaultdict
from itertools import groupby
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

# ---- Pha 2: ngưỡng sinh ứng viên (chọn bằng cách đo trên lô sạch, xem REPORT.md)
# Vật quá nhỏ thì mắt người không kiểm được; bỏ qua để hàng đợi gọn.
CAND_MIN_SEG_FRAC = 0.003     # segment >= 0,3% diện tích ảnh mới xét
SWAP_MIN_IOU = 0.70           # box và segment khác lớp trùng vị trí (IoU bbox)
SWAP_SAME_CLASS_IOU = 0.50    # ... và không có cặp cùng lớp nào khớp ở chỗ đó
# Lô sạch: mọi cặp box-segment đúng có >= 98% pixel segment nằm trong box (box COCO bao trọn vật).
DRIFT_MAX_COVER = 0.97        # < 97% pixel của segment nằm trong box đã ghép -> lệch
DRIFT_LOOSE_COVER = 0.995     # hoặc: có pixel rơi ra ngoài (box phóng to rồi lệch) ...
DRIFT_MAX_IOU = 0.75          # ... và IoU giữa box với bbox của segment thấp
MISSING_MIN_SEG_FRAC = 0.003  # segment không có box: chỉ báo khi >= 0,3% ảnh
MISSING_MAX_COVER = 0.50      # ... và không box cùng lớp còn trống nào chứa quá nửa segment
MERGE_PART_MIN = 0.15         # mỗi box chứa >= 15% pixel của segment
MERGE_BEST_MAX = 0.85         # nhưng không box nào chứa trọn (>= 85%)
MERGE_UNION_MIN = 0.85        # các box gộp lại phủ gần hết segment


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
        self._runs_cache: dict[str, dict[int, list[tuple[int, int, int]]]] = {}

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
            runs = self.mask_runs(name, mask_path)
            counts = {sid: sum(x1 - x0 for _, x0, x1 in rs) for sid, rs in runs.items()}
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

    def mask_runs(self, name: str, path: Path) -> dict[int, list[tuple[int, int, int]]]:
        """Mask -> {segment_id: [(y, x0, x1), ...]}: các đoạn pixel liên tiếp trên từng dòng (x1 không tính).
        Đếm pixel và đếm 'pixel nằm trong box' trên các đoạn này nhanh hơn duyệt từng pixel."""
        if name in self._runs_cache:
            return self._runs_cache[name]
        width, height, pixels = pngutil.read_rgb(path)
        raw = bytes(pixels)
        runs: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        stride = width * 3
        for y in range(height):
            row = raw[y * stride:(y + 1) * stride]
            ids = [r + g * 256 + b * 65536 for r, g, b in zip(row[0::3], row[1::3], row[2::3])]
            x = 0
            for sid, grp in groupby(ids):
                n = sum(1 for _ in grp)
                if sid:  # id 0 là nền
                    runs[sid].append((y, x, x + n))
                x += n
        self._runs_cache[name] = runs
        return runs

    # ------------------------------------------------------------ Pha 2: ứng viên
    def build_candidates(self) -> list[dict]:
        cat_by_id = {c["id"]: c for c in self.panoptic.get("categories", [])}
        # ảnh đã mang lỗi luật thì không mang thêm lỗi gán nhãn (mỗi ảnh nhiều nhất một lỗi)
        flagged = {e["target"] for e in self.errors if e["type"] != "scene_leak"}
        out: list[dict] = []
        for ann in self.panoptic.get("annotations", []):
            name = Path(ann.get("file_name", "")).stem
            if name in flagged or name not in self.manifest:
                continue
            size = self.image_size(name)
            mask_path = self._mask_path(name, ann)
            label_rel = self.manifest[name].get("label")
            if size is None or mask_path is None or not label_rel or not (self.d / label_rel).is_file():
                continue
            runs = self.mask_runs(name, mask_path)
            segs = []
            for s in ann.get("segments_info", []):
                cat = cat_by_id.get(s.get("category_id"))
                if not cat or not cat.get("isthing") or s.get("iscrowd"):
                    continue
                rs = runs.get(int(s["id"]), [])
                if not rs:
                    continue
                xs0, xs1 = min(r[1] for r in rs), max(r[2] for r in rs)
                ys0, ys1 = min(r[0] for r in rs), max(r[0] for r in rs) + 1
                segs.append({"id": int(s["id"]), "cls": cat["name"], "runs": rs,
                             "area": sum(r[2] - r[1] for r in rs), "bbox": (xs0, ys0, xs1, ys1)})
            boxes = self._boxes_px(self.d / label_rel, size)
            out.extend(self._image_candidates(name, size, boxes, segs))
        out.sort(key=lambda c: (-c["score"], c["image"]))
        return out

    def _boxes_px(self, path: Path, size: tuple[int, int]) -> list[dict]:
        W, H = size
        boxes = []
        for i, row in enumerate(read_yolo(path)):
            try:
                cls = int(row[0])
                x, y, w, h = map(float, row[1:5])
            except (ValueError, IndexError):
                continue
            if cls not in self.classes:
                continue
            x0, y0 = max(0.0, (x - w / 2) * W), max(0.0, (y - h / 2) * H)
            x1, y1 = min(float(W), (x + w / 2) * W), min(float(H), (y + h / 2) * H)
            if x1 > x0 and y1 > y0:
                boxes.append({"idx": i, "cls": self.classes[cls], "bbox": (x0, y0, x1, y1)})
        return boxes

    def _image_candidates(self, name, size, boxes, segs) -> list[dict]:
        W, H = size
        img_area = W * H
        found: list[dict] = []

        def cand(kind, score, **detail):
            found.append({"image": name, "type": kind, "score": round(score, 3), **detail})

        big = [s for s in segs if s["area"] >= CAND_MIN_SEG_FRAC * img_area]
        inside = {(b["idx"], s["id"]): pixels_in_box(s["runs"], b["bbox"]) for b in boxes for s in big}
        iou = {(b["idx"], s["id"]): box_iou(b["bbox"], s["bbox"]) for b in boxes for s in big}

        # 1) swap: box và segment trùng vị trí nhưng khác lớp, chỗ đó không có cặp cùng lớp
        swapped_boxes, swapped_segs = set(), set()
        for b in boxes:
            best = max(big, key=lambda s: iou[(b["idx"], s["id"])], default=None)
            if best is None or best["cls"] == b["cls"] or iou[(b["idx"], best["id"])] < SWAP_MIN_IOU:
                continue
            if any(s["cls"] == b["cls"] and iou[(b["idx"], s["id"])] >= SWAP_SAME_CLASS_IOU for s in big):
                continue
            if any(o["cls"] == best["cls"] and iou[(o["idx"], best["id"])] >= SWAP_SAME_CLASS_IOU for o in boxes):
                continue
            swapped_boxes.add(b["idx"])
            swapped_segs.add(best["id"])
            cand("swap", iou[(b["idx"], best["id"])], options=["class_swap", "category_swap"],
                 det_class=b["cls"], pan_class=best["cls"], box=rnd(b["bbox"]), segment_id=best["id"],
                 segment_bbox=list(best["bbox"]),
                 reason=f"box detection '{b['cls']}' trùng segment panoptic '{best['cls']}' (IoU {iou[(b['idx'], best['id'])]:.2f})")

        # 2) ghép cùng lớp: tham lam theo số pixel của segment nằm trong box
        pairs = sorted(((inside[(b["idx"], s["id"])], b, s) for b in boxes for s in big
                        if b["cls"] == s["cls"] and inside[(b["idx"], s["id"])] > 0), key=lambda t: -t[0])
        box_of, seg_of = {}, {}
        for n, b, s in pairs:
            if b["idx"] in box_of or s["id"] in seg_of:
                continue
            if n < 0.3 * s["area"] and n < 0.3 * box_area(b["bbox"]):
                continue
            box_of[b["idx"]], seg_of[s["id"]] = s, b

        # Đếm theo lớp (bỏ cặp đã nghi swap): gộp hai vật làm số box > số segment,
        # thiếu box làm số segment > số box. Lô sạch: không lớp nào có box > segment.
        n_box = Counter(b["cls"] for b in boxes if b["idx"] not in swapped_boxes)
        n_seg = Counter(s["cls"] for s in segs if s["id"] not in swapped_segs)

        # 3) instance_merge: lớp có box nhiều hơn segment, và một segment phủ box "thừa" đó
        merged = set()
        merge_boxes = set()
        merged_cls = set()
        for s in sorted(big, key=lambda s: -s["area"]):
            if s["id"] in swapped_segs or n_box[s["cls"]] <= n_seg[s["cls"]] or s["cls"] in merged_cls:
                continue
            parts = [b for b in boxes if b["cls"] == s["cls"] and b["idx"] not in swapped_boxes
                     and inside[(b["idx"], s["id"])] >= MERGE_PART_MIN * min(s["area"], box_area(b["bbox"]))]
            if len(parts) < 2:
                continue
            best = max(inside[(b["idx"], s["id"])] for b in parts) / s["area"]
            union = pixels_in_boxes(s["runs"], [b["bbox"] for b in parts]) / s["area"]
            spare = [b for b in parts if b["idx"] not in box_of or box_of[b["idx"]]["id"] == s["id"]]
            if union >= MERGE_UNION_MIN and (best < MERGE_BEST_MAX or len(spare) >= 1):
                merged.add(s["id"])
                merged_cls.add(s["cls"])
                merge_boxes.update(b["idx"] for b in parts)
                cand("instance_merge", (union - best) + 0.5, segment_id=s["id"], pan_class=s["cls"],
                     segment_bbox=list(s["bbox"]), boxes=[rnd(b["bbox"]) for b in parts],
                     reason=f"lớp '{s['cls']}': {n_box[s['cls']]} box nhưng {n_seg[s['cls']]} segment; "
                            f"segment phủ {len(parts)} box (box lớn nhất chứa {best:.0%})")

        # 4) bbox_drift: cặp cùng lớp mà nhiều pixel của segment rơi ra ngoài box
        for b in boxes:
            s = box_of.get(b["idx"])
            if s is None or s["id"] in merged or b["idx"] in swapped_boxes or b["idx"] in merge_boxes:
                continue
            cover = inside[(b["idx"], s["id"])] / s["area"]
            overlap = iou[(b["idx"], s["id"])]
            if cover < DRIFT_MAX_COVER or (cover < DRIFT_LOOSE_COVER and overlap < DRIFT_MAX_IOU):
                cand("bbox_drift", (1 - cover) + (1 - overlap), det_class=b["cls"], box=rnd(b["bbox"]),
                     segment_id=s["id"], segment_bbox=list(s["bbox"]),
                     reason=f"chỉ {cover:.0%} pixel của segment '{s['cls']}' nằm trong box (IoU {overlap:.2f})")

        # 5) missing_box: lớp có segment nhiều hơn box; báo segment lớn nhất không ghép được box riêng.
        #    (vật nằm trong box của vật khác lớp là bình thường: cốc trong box bàn ăn)
        for cls in sorted(n_seg):
            if n_seg[cls] <= n_box[cls]:
                continue
            lonely = [s for s in big if s["cls"] == cls and s["id"] not in seg_of
                      and s["id"] not in swapped_segs and s["id"] not in merged
                      and s["area"] >= MISSING_MIN_SEG_FRAC * img_area]
            free = [b for b in boxes if b["cls"] == cls and b["idx"] not in box_of]
            lonely = [s for s in lonely
                      if max((inside[(b["idx"], s["id"])] for b in free), default=0) <= MISSING_MAX_COVER * s["area"]]
            if not lonely:
                continue
            s = max(lonely, key=lambda s: s["area"])
            cand("missing_box", min(1.0, s["area"] / img_area * 10) + 0.5, pan_class=cls, segment_id=s["id"],
                 segment_bbox=list(s["bbox"]),
                 reason=f"lớp '{cls}': {n_seg[cls]} segment nhưng {n_box[cls]} box; segment {s['area']} px không có box riêng")
        return found

    # ------------------------------------------------------------ chạy
    def run(self) -> list[dict]:
        self.check_files()
        self.check_labels()
        self.check_metadata()
        self.check_panoptic()
        return self.errors


# ---------------------------------------------------------------- hình học
def rnd(b) -> list[float]:
    return [round(v, 1) for v in b]


def box_area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def box_iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


def _px_range(lo: float, hi: float) -> tuple[int, int]:
    """Pixel x có tâm (x + 0,5) nằm trong [lo, hi)."""
    return math.ceil(lo - 0.5), math.ceil(hi - 0.5)


def pixels_in_box(runs, box) -> int:
    bx0, bx1 = _px_range(box[0], box[2])
    by0, by1 = _px_range(box[1], box[3])
    total = 0
    for y, x0, x1 in runs:
        if by0 <= y < by1:
            n = min(x1, bx1) - max(x0, bx0)
            if n > 0:
                total += n
    return total


def pixels_in_boxes(runs, boxes) -> int:
    """Số pixel của segment nằm trong ít nhất một box (hợp các box)."""
    ranges = [(_px_range(b[0], b[2]), _px_range(b[1], b[3])) for b in boxes]
    total = 0
    for y, x0, x1 in runs:
        spans = sorted((max(x0, bx0), min(x1, bx1)) for (bx0, bx1), (by0, by1) in ranges if by0 <= y < by1)
        end = -1
        for a, c in spans:
            if c <= a:
                continue
            a = max(a, end)
            if c > a:
                total += c - a
                end = c
    return total

def main() -> int:
    ap = argparse.ArgumentParser(description="Validator lỗi luật cho lô bàn giao Day 10")
    ap.add_argument("delivery", help="thư mục delivery/ của lô")
    ap.add_argument("--out", required=True, help="file errors.json đầu ra")
    ap.add_argument("--candidates", help="file candidates.json: ứng viên lỗi gán nhãn để xem ảnh (Pha 2)")
    args = ap.parse_args()

    delivery = Path(args.delivery)
    if not (delivery / "manifest.json").is_file():
        ap.error(f"không thấy manifest.json trong {delivery}")

    validator = Validator(delivery)
    errors = validator.run()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"errors": errors}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = Counter(e["type"] for e in errors)
    print(f"{len(errors)} lỗi luật -> {out}")
    for kind, n in sorted(summary.items()):
        print(f"  {kind}: {n}")

    if args.candidates:
        cands = validator.build_candidates()
        cand = Path(args.candidates)
        cand.parent.mkdir(parents=True, exist_ok=True)
        cand.write_text(json.dumps({"candidates": cands}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        by_type = Counter(c["type"] for c in cands)
        print(f"{len(cands)} ứng viên trên {len({c['image'] for c in cands})} ảnh -> {cand}")
        for kind, n in sorted(by_type.items()):
            print(f"  {kind}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
