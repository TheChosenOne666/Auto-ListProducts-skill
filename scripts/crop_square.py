#!/usr/bin/env python3
"""Crop square product images from phone screenshots, with quality guards.

Guards
  1. blank crop  - the product photo never rendered in the screenshot
  2. carousel dots - the square window catches the image pager dots at the bottom;
                     the crop is capped above the dots and white-padded back to square

Usage
  python crop_square.py --src shots --dst images --products products.json \
      --tops "1440x3200:285,1280x2844:245" --size 1280 \
      --sheet crops.png --report crop-report.json

products.json entries accepted (extra keys are ignored):
  {"n": 1, "name": "示例商品500ml", "img": "示例商品500ml.jpg",
   "src": "abc123.jpg", "alt_src": "def456.jpg"}
  - src      source screenshot file name inside --src (defaults to <n-th file> order)
  - alt_src  duplicate screenshot to use when the primary one is blank

Exit codes: 0 all good, 2 blanks found, 1 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image

DEFAULT_TOPS = "1440x3200:285,1280x2844:245"


def parse_tops(spec: str) -> dict[tuple[int, int], int]:
    out = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        size, top = part.split(":")
        w, h = size.lower().split("x")
        out[(int(w), int(h))] = int(top)
    return out


def row_content(im: Image.Image, y: int, w: int, band: int = 6) -> int:
    strip = im.crop((0, y, w, min(y + band, im.height))).resize((200, 1))
    return sum(1 for c in strip.getdata() if sum(c) < 700)


def unique_colors(im: Image.Image, step: int = 53) -> int:
    return len(set(list(im.getdata())[::step]))


def find_dots(im: Image.Image, w: int, top: int) -> int | None:
    """First row inside the square window that follows a blank gap and has content."""
    for y in range(top + int(w * 0.90), top + w - 6, 4):
        if row_content(im, y, w) > 0 and row_content(im, y - 18, w) == 0:
            return y
    return None


def crop_one(src_path: str, top: int, size: int, fix_dots: bool) -> tuple[Image.Image, str, int]:
    im = Image.open(src_path).convert("RGB")
    w = im.width
    window_bottom = top + w
    if window_bottom > im.height:
        raise SystemExit(f"{src_path}: window {top}..{window_bottom} exceeds height {im.height}")
    status = "ok"
    dy = find_dots(im, w, top) if fix_dots else None
    if dy and (window_bottom - dy) >= 4:
        body = im.crop((0, top, w, dy - 12))
        canvas = Image.new("RGB", (w, w), "white")
        scaled = body.resize((w, max(1, int(body.height * w / body.width))), Image.LANCZOS)
        canvas.paste(scaled, (0, (w - scaled.height) // 2))
        im = canvas
        status = "dots-fixed"
    else:
        im = im.crop((0, top, w, window_bottom))
    im = im.resize((size, size), Image.LANCZOS)
    return im, status, unique_colors(im)


def contact_sheet(paths: list[str], out: str, cols: int = 6, cell: int = 240) -> None:
    if not paths:
        return
    rows = (len(paths) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), "white")
    for i, p in enumerate(paths):
        with Image.open(p) as im:
            sheet.paste(im.resize((cell - 6, cell - 6)), ((i % cols) * cell + 3, (i // cols) * cell + 3))
    sheet.save(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="folder with source screenshots")
    ap.add_argument("--dst", required=True, help="folder for cropped images")
    ap.add_argument("--products", required=True, help="products.json manifest")
    ap.add_argument("--tops", default=DEFAULT_TOPS, help='per-resolution crop tops, e.g. "1440x3200:285,1280x2844:245"')
    ap.add_argument("--size", type=int, default=1280, help="output edge length (default 1280)")
    ap.add_argument("--min-colors", type=int, default=200, help="below this many sampled colors a crop counts as blank")
    ap.add_argument("--no-dots-fix", action="store_true", help="disable carousel-dot capping")
    ap.add_argument("--sheet", help="write a contact sheet here")
    ap.add_argument("--report", help="write a JSON report here")
    args = ap.parse_args()

    tops = parse_tops(args.tops)
    products = json.load(open(args.products, encoding="utf-8"))
    os.makedirs(args.dst, exist_ok=True)

    report, blanks, made = [], [], []
    for p in products:
        src_name = p.get("src")
        if not src_name:
            print(f"skip {p.get('name')!r}: no src field", file=sys.stderr)
            continue
        src_path = os.path.join(args.src, src_name)
        if not os.path.exists(src_path):
            report.append({"n": p.get("n"), "name": p.get("name"), "status": "missing-src", "src": src_name})
            continue
        with Image.open(src_path) as probe:
            size_wh = probe.size
        top = tops.get(size_wh)
        if top is None:
            report.append({"n": p.get("n"), "name": p.get("name"), "status": "no-top-for-size",
                           "size": list(size_wh)})
            continue
        try:
            im, status, colors = crop_one(src_path, top, args.size, not args.no_dots_fix)
        except SystemExit as exc:
            report.append({"n": p.get("n"), "name": p.get("name"), "status": "error", "error": str(exc)})
            continue
        if colors < args.min_colors:
            status = "blank"
            blanks.append(p.get("name"))
        out_name = p.get("img") or (str(p.get("name")) + ".jpg")
        out_path = os.path.join(args.dst, out_name)
        im.save(out_path, quality=92)
        made.append(out_path)
        report.append({"n": p.get("n"), "name": p.get("name"), "img": out_name,
                       "status": status, "colors": colors, "bytes": os.path.getsize(out_path)})

    if args.sheet:
        contact_sheet(made, args.sheet)
    if args.report:
        json.dump({"tops": {f"{k[0]}x{k[1]}": v for k, v in tops.items()}, "items": report},
                  open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    counts: dict[str, int] = {}
    for r in report:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("cropped:", len(made), dict(counts))
    if blanks:
        print("BLANK (re-crop from an alternate screenshot or ask the user):")
        for b in blanks:
            print("  -", b)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
