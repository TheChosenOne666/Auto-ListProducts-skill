#!/usr/bin/env python3
"""Crop square product images from phone screenshots, with quality guards.

Guards
  1. blank crop  - the product photo never rendered in the screenshot. The crop is never
                   written; the entry falls back to `alt_src`, and only a still-blank
                   result is reported.
  2. carousel dots - the square window is taller than the photo area, so the image pager
                     dots leak into the bottom edge. The crop is capped above the dots and
                     white-padded back to a square.

Usage
  python crop_square.py --src shots --dst images --products products.json \
      --windows "1440x3200:285,1280x2844:280:1500" --size 1280 \
      --sheet crops.png --report crop-report.json

--windows maps a source resolution to "top" or "top:bottom" of the photo area
  (phone screenshots carry a fixed title bar; a bottom bound is used when the photo area
  is shorter than the image width, the remainder is padded white and centred).

products.json entries accepted (extra keys are ignored):
  {"n": 1, "name": "示例商品500ml", "img": "示例商品500ml.jpg",
   "src": "abc123.jpg", "alt_src": "def456.jpg"}
  - src      source screenshot file name inside --src
  - alt_src  duplicate screenshot used when `src` crops blank

Exit codes: 0 all good, 2 blanks or unusable sources found, 1 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image

DEFAULT_WINDOWS = "1440x3200:285,1280x2844:245"


def parse_windows(spec: str) -> dict[tuple[int, int], tuple[int, int | None]]:
    out: dict[tuple[int, int], tuple[int, int | None]] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        fields = part.split(":")
        if len(fields) == 2:
            size, top = fields
            out[_size(size)] = (int(top), None)
        elif len(fields) == 3:
            size, top, bottom = fields
            out[_size(size)] = (int(top), int(bottom))
        else:
            raise SystemExit(f"bad --windows entry {part!r}, want WxH:top or WxH:top:bottom")
    return out


def _size(spec: str) -> tuple[int, int]:
    w, h = spec.lower().split("x")
    return int(w), int(h)


def row_content(im: Image.Image, y: int, w: int, band: int = 6) -> int:
    strip = im.crop((0, y, w, min(y + band, im.height))).resize((200, 1))
    return sum(1 for c in strip.getdata() if sum(c) < 700)


def unique_colors(im: Image.Image, step: int = 53) -> int:
    return len(set(list(im.getdata())[::step]))


def find_dots(im: Image.Image, w: int, top: int, bottom: int) -> int | None:
    """First row in the window that follows a blank gap and has content (the pager dots)."""
    span = bottom - top
    for y in range(top + int(span * 0.9), bottom - 6, 4):
        if row_content(im, y, w) > 0 and row_content(im, y - 18, w) == 0:
            return y
    return None


def crop_one(src_path: str, top: int, bottom: int | None, size: int, fix_dots: bool) -> tuple[Image.Image, str, int]:
    im = Image.open(src_path).convert("RGB")
    w = im.width
    window_bottom = bottom if bottom else top + w
    if window_bottom > im.height:
        raise ValueError(f"{src_path}: window {top}..{window_bottom} exceeds height {im.height}")
    status = "ok"
    dy = find_dots(im, w, top, window_bottom) if fix_dots else None
    if dy and (window_bottom - dy) >= 4:
        window_bottom = dy - 12
        status = "dots-capped"
    body = im.crop((0, top, w, window_bottom))
    if body.height != w:
        canvas = Image.new("RGB", (w, w), "white")
        scaled = body.resize((w, max(1, int(body.height * w / body.width))), Image.LANCZOS)
        canvas.paste(scaled, (0, (w - scaled.height) // 2))
        im = canvas
        status = f"{status}+padded"
    else:
        im = body
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


def crop_with_fallback(p: dict, src_dir: str, windows, args) -> tuple[dict, Image.Image | None]:
    """Try `src`, then `alt_src`. Returns the report entry and the image (None if unusable)."""
    sources = [s for s in (p.get("src"), p.get("alt_src")) if s]
    if not sources:
        return ({"n": p.get("n"), "name": p.get("name"), "status": "no-src"}, None)
    entry = {"n": p.get("n"), "name": p.get("name")}
    notes = []
    for attempt, src_name in enumerate(sources):
        src_path = os.path.join(src_dir, src_name)
        if not os.path.exists(src_path):
            notes.append(f"missing-src:{src_name}")
            continue
        with Image.open(src_path) as probe:
            resolution = probe.size
        window = windows.get(resolution)
        if window is None:
            notes.append(f"no-window-for:{resolution[0]}x{resolution[1]}")
            continue
        top, bottom = window
        try:
            im, status, colors = crop_one(src_path, top, bottom, args.size, not args.no_dots_fix)
        except (ValueError, OSError) as exc:
            notes.append(f"error:{exc}")
            continue
        if colors < args.min_colors:
            notes.append(f"blank:{src_name}(colors={colors})")
            continue
        entry.update({"img": p.get("img") or f"{p.get('name')}.jpg", "src": src_name,
                      "attempt": attempt + 1, "status": status, "colors": colors})
        if notes:
            entry["rejected"] = notes
        return (entry, im)
    entry["status"] = "blank" if any(n.startswith("blank") for n in notes) else "unusable"
    entry["rejected"] = notes
    return (entry, None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="folder with source screenshots")
    ap.add_argument("--dst", required=True, help="folder for cropped images")
    ap.add_argument("--products", required=True, help="products.json manifest")
    ap.add_argument("--windows", default=DEFAULT_WINDOWS,
                    help='per-resolution crop bounds, e.g. "1440x3200:285,1280x2844:280:1500"')
    ap.add_argument("--size", type=int, default=1280, help="output edge length (default 1280)")
    ap.add_argument("--min-colors", type=int, default=200, help="below this many sampled colors a crop counts as blank")
    ap.add_argument("--no-dots-fix", action="store_true", help="disable carousel-dot capping")
    ap.add_argument("--sheet", help="write a contact sheet here")
    ap.add_argument("--report", help="write a JSON report here")
    args = ap.parse_args()

    windows = parse_windows(args.windows)
    products = json.load(open(args.products, encoding="utf-8"))
    os.makedirs(args.dst, exist_ok=True)

    report, bad, made = [], [], []
    for p in products:
        entry, im = crop_with_fallback(p, args.src, windows, args)
        report.append(entry)
        if im is None:
            bad.append(entry)
            continue
        out_path = os.path.join(args.dst, entry["img"])
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        im.save(out_path, quality=92)
        entry["bytes"] = os.path.getsize(out_path)
        made.append(out_path)

    if args.sheet:
        contact_sheet(made, args.sheet)
    if args.report:
        json.dump({"windows": {f"{k[0]}x{k[1]}": list(v) for k, v in windows.items()}, "items": report},
                  open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    counts: dict[str, int] = {}
    for r in report:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("cropped:", len(made), counts)
    if bad:
        print("NOT WRITTEN (re-crop from another screenshot or ask the user to re-send):")
        for r in bad:
            print(f"  - {r.get('n')} {r.get('name')}: {r['status']}"
                  f"{' rejected ' + ', '.join(r['rejected']) if r.get('rejected') else ''}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
