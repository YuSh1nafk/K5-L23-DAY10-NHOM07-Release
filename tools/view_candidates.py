#!/usr/bin/env python3
"""Công cụ xem ảnh cho Pha 2: vẽ box detection và viền segment panoptic lên ảnh để review.

  # vẽ mọi ứng viên trong candidates.json, ghi PNG + index.html vào thư mục --out
  python tools/view_candidates.py runs/r7007/delivery --candidates runs/r7007/candidates.json --out runs/r7007/review_png

  # vẽ một hoặc vài ảnh bất kỳ (không cần candidates.json)
  python tools/view_candidates.py runs/r7007/delivery --image 000000013177 000000006471 --out runs/r7007/review_png

Mỗi ảnh ra một PNG gồm hai nửa:
  trái : toàn ảnh, mọi box detection (VÀNG, nhãn "D:<lớp>") và viền segment thing (XANH, nhãn "P:<lớp>")
  phải : vùng nghi ngờ được cắt và phóng to; box ứng viên ĐỎ, segment ứng viên tô HỒNG
Mở index.html để xem lần lượt, kèm lý do validator đưa ra.

Công cụ này được dùng Pillow (RULES cho phép với công cụ xem ảnh). validator/validate.py thì KHÔNG.
Đây chỉ là công cụ hỗ trợ: kết luận confirmed/rejected là của người xem ảnh.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

DET_COLOR = (255, 210, 0)       # box detection
PAN_COLOR = (0, 220, 255)       # viền segment panoptic
HL_BOX = (255, 40, 40)          # box của ứng viên
HL_SEG = (255, 0, 200)          # segment của ứng viên
PANEL = 640                     # chiều dài cạnh lớn của mỗi nửa ảnh
BAR = 54                        # dải chú thích phía trên


def font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


class Delivery:
    def __init__(self, root: Path):
        self.root = root
        self.manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["items"]
        self.classes = {}
        for line in (root / "classes.txt").read_text(encoding="utf-8").splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0].isdigit():
                self.classes[int(parts[0])] = parts[1]
        pan = json.loads((root / "panoptic.json").read_text(encoding="utf-8"))
        self.cats = {c["id"]: c for c in pan.get("categories", [])}
        self.anns = {Path(a["file_name"]).stem: a for a in pan.get("annotations", [])}

    def image(self, name: str) -> Image.Image | None:
        rel = self.manifest.get(name, {}).get("image", f"images/{name}.jpg")
        path = self.root / rel
        return Image.open(path).convert("RGB") if path.is_file() else None

    def mask(self, name: str) -> Image.Image | None:
        rel = self.manifest.get(name, {}).get("mask")
        if not rel and name in self.anns:
            rel = f"masks/{self.anns[name]['file_name']}"
        path = self.root / rel if rel else None
        return Image.open(path).convert("RGB") if path and path.is_file() else None

    def boxes(self, name: str, size: tuple[int, int]) -> list[dict]:
        rel = self.manifest.get(name, {}).get("label", f"labels/{name}.txt")
        path = self.root / rel
        if not path.is_file():
            return []
        W, H = size
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue
            try:
                cls = int(parts[0])
                x, y, w, h = map(float, parts[1:])
            except ValueError:
                continue
            out.append({"cls": self.classes.get(cls, f"?{cls}"),
                        "bbox": ((x - w / 2) * W, (y - h / 2) * H, (x + w / 2) * W, (y + h / 2) * H)})
        return out

    def segments(self, name: str) -> list[dict]:
        ann = self.anns.get(name, {})
        out = []
        for s in ann.get("segments_info", []):
            cat = self.cats.get(s.get("category_id"))
            if cat and not cat.get("isthing"):
                continue  # chỉ vẽ thing; stuff (trời, đường...) làm rối hình
            out.append({"id": int(s["id"]), "cls": cat["name"] if cat else f"?{s.get('category_id')}",
                        "crowd": bool(s.get("iscrowd"))})
        return out


def seg_color(sid: int) -> tuple[int, int, int]:
    return sid % 256, (sid // 256) % 256, (sid // 65536) % 256


def binary(mask: Image.Image, sid: int) -> Image.Image:
    """Ảnh L: 255 ở pixel thuộc segment sid."""
    solid = Image.new("RGB", mask.size, seg_color(sid))
    r, g, b = ImageChops.difference(mask, solid).split()
    diff = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return diff.point(lambda v: 255 if v == 0 else 0)


def outline(bin_mask: Image.Image, width: int) -> Image.Image:
    edge = ImageChops.subtract(bin_mask, bin_mask.filter(ImageFilter.MinFilter(3)))
    for _ in range(max(0, width - 1)):
        edge = edge.filter(ImageFilter.MaxFilter(3))
    return edge


def bbox_of(bin_mask: Image.Image):
    return bin_mask.getbbox()


def render(img, mask, boxes, segs, region, scale, hl_box, hl_seg, f):
    """Cắt region (x0,y0,x1,y1) của ảnh, phóng theo scale, rồi vẽ box + viền segment."""
    x0, y0, x1, y1 = region
    size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
    canvas = img.crop(region).resize(size, Image.LANCZOS)
    msk = mask.crop(region).resize(size, Image.NEAREST) if mask is not None else None
    line = max(2, round(scale)) if scale > 1 else 2
    draw = ImageDraw.Draw(canvas)

    if msk is not None:
        for s in segs:
            b = binary(msk, s["id"])
            if b.getbbox() is None:
                continue
            if s["id"] in hl_seg:
                tint = Image.new("RGB", size, HL_SEG)
                canvas.paste(Image.blend(canvas, tint, 0.45), (0, 0), b)
            color = HL_SEG if s["id"] in hl_seg else PAN_COLOR
            canvas.paste(Image.new("RGB", size, color), (0, 0), outline(b, line))
        draw = ImageDraw.Draw(canvas)
        for s in segs:
            b = binary(msk, s["id"])
            bb = b.getbbox()
            if bb is None:
                continue
            label = f"P:{s['cls']}" + (" (crowd)" if s["crowd"] else "")
            tag(draw, (bb[0], bb[3]), label, HL_SEG if s["id"] in hl_seg else PAN_COLOR, f, below=True, limit=size)

    for i, bx in enumerate(boxes):
        bx0, by0, bx1, by1 = [(v - o) * scale for v, o in zip(bx["bbox"], (x0, y0, x0, y0))]
        color = HL_BOX if i in hl_box else DET_COLOR
        draw.rectangle([bx0, by0, bx1, by1], outline=color, width=line + (1 if i in hl_box else 0))
        tag(draw, (bx0, by0), f"D:{bx['cls']}", color, f, below=False, limit=size)
    return canvas


def tag(draw, xy, text, color, f, below, limit):
    x, y = xy
    l, t, r, b = draw.textbbox((0, 0), text, font=f)
    w, h = r - l + 6, b - t + 4
    x = min(max(0, x), max(0, limit[0] - w))
    y = y if below else y - h
    y = min(max(0, y), max(0, limit[1] - h))
    draw.rectangle([x, y, x + w, y + h], fill=(0, 0, 0))
    draw.text((x + 3, y + 2 - t), text, fill=color, font=f)


def match_highlights(cand, boxes, segs):
    """Tìm box/segment mà ứng viên nhắc tới để tô nổi."""
    hl_box, hl_seg = set(), set()
    if cand.get("segment_id") is not None:
        hl_seg.add(int(cand["segment_id"]))
    wanted = [cand["box"]] if cand.get("box") else list(cand.get("boxes", []))
    for w in wanted:
        best = min(range(len(boxes)), key=lambda i: sum(abs(a - b) for a, b in zip(boxes[i]["bbox"], w)), default=None)
        if best is not None:
            hl_box.add(best)
    return hl_box, hl_seg


def focus_region(cand, boxes, hl_box, img_size, mask, hl_seg):
    W, H = img_size
    rects = [boxes[i]["bbox"] for i in hl_box]
    if cand.get("segment_bbox"):
        rects.append(tuple(cand["segment_bbox"]))
    elif mask is not None:
        for sid in hl_seg:
            bb = binary(mask, sid).getbbox()
            if bb:
                rects.append(bb)
    if not rects:
        return 0, 0, W, H
    x0 = min(r[0] for r in rects); y0 = min(r[1] for r in rects)
    x1 = max(r[2] for r in rects); y1 = max(r[3] for r in rects)
    pad = max(16, 0.25 * max(x1 - x0, y1 - y0))
    x0, y0 = max(0, int(x0 - pad)), max(0, int(y0 - pad))
    x1, y1 = min(W, int(x1 + pad + 1)), min(H, int(y1 + pad + 1))
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def draw_candidate(dv: Delivery, cand: dict, out_path: Path, f_small, f_big) -> bool:
    name = cand["image"]
    img = dv.image(name)
    if img is None:
        return False
    mask = dv.mask(name)
    boxes = dv.boxes(name, img.size)
    segs = dv.segments(name)
    hl_box, hl_seg = match_highlights(cand, boxes, segs)

    full_scale = PANEL / max(img.size)
    left = render(img, mask, boxes, segs, (0, 0, *img.size), full_scale, hl_box, hl_seg, f_small)
    region = focus_region(cand, boxes, hl_box, img.size, mask, hl_seg)
    zoom = min(PANEL / max(region[2] - region[0], region[3] - region[1]), 8.0)
    right = render(img, mask, boxes, segs, region, zoom, hl_box, hl_seg, f_small)

    width = left.width + right.width + 12
    height = BAR + max(left.height, right.height)
    sheet = Image.new("RGB", (width, height), (24, 24, 24))
    sheet.paste(left, (0, BAR))
    sheet.paste(right, (left.width + 12, BAR))
    d = ImageDraw.Draw(sheet)
    title = f"{name}  [{cand.get('type', 'xem')}]  {img.size[0]}x{img.size[1]}"
    d.text((8, 4), title, fill=(255, 255, 255), font=f_big)
    d.text((8, 30), "VÀNG = box detection (D:)   XANH = viền panoptic (P:)   ĐỎ/HỒNG = box/segment ứng viên",
           fill=(200, 200, 200), font=f_small)
    sheet.save(out_path)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Vẽ box detection + viền segment panoptic để review ứng viên")
    ap.add_argument("delivery", help="thư mục delivery/")
    ap.add_argument("--candidates", help="candidates.json do validator ghi")
    ap.add_argument("--image", nargs="*", default=[], help="tên ảnh muốn xem thêm (không đuôi)")
    ap.add_argument("--out", required=True, help="thư mục ghi PNG và index.html")
    args = ap.parse_args()

    dv = Delivery(Path(args.delivery))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cands = []
    if args.candidates:
        cands = json.loads(Path(args.candidates).read_text(encoding="utf-8"))["candidates"]
    cands += [{"image": n, "type": "xem", "reason": "xem theo yêu cầu"} for n in args.image]
    if not cands:
        ap.error("cần --candidates hoặc --image")

    f_small, f_big = font(14), font(20)
    rows = []
    for i, c in enumerate(cands, start=1):
        png = f"{i:03d}_{c['image']}_{c.get('type', 'xem')}.png"
        ok = draw_candidate(dv, c, out / png, f_small, f_big)
        rows.append((c, png if ok else None))
        print(f"[{i}/{len(cands)}] {c['image']} {c.get('type')} -> {png if ok else 'không có ảnh'}")

    # bảng nháp để điền review: verdict/note do NGƯỜI xem ảnh ghi, không tự điền
    with (out / "review_todo.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "type", "verdict", "note", "validator_reason"])
        for c, _ in rows:
            kind = "class_swap|category_swap" if c.get("type") == "swap" else c.get("type", "")
            w.writerow([c["image"], kind, "", "", c.get("reason", "")])

    items = []
    for c, png in rows:
        opts = " hoặc ".join(c.get("options", [])) or c.get("type", "")
        img_html = f'<img src="{html.escape(png)}" loading="lazy">' if png else "<p><b>Không có file ảnh</b></p>"
        items.append(f'<section><h2>{html.escape(c["image"])} — {html.escape(opts)}</h2>'
                     f'<p>{html.escape(c.get("reason", ""))}</p>{img_html}</section>')
    (out / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Review ứng viên</title>"
        "<style>body{background:#111;color:#eee;font-family:sans-serif;margin:16px}"
        "img{max-width:100%;border:1px solid #444}section{margin-bottom:40px}</style>"
        f"<h1>Review ứng viên ({len(rows)})</h1>"
        "<p>Xem ảnh rồi ghi review_log.csv: verdict confirmed/rejected, note nói rõ THẤY GÌ và ai xem.</p>"
        + "".join(items), encoding="utf-8")
    print(f"xong: mở {out / 'index.html'}; bảng nháp {out / 'review_todo.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
