#!/usr/bin/env python3
"""Audit product images for "blank" uploads.

A white crop compresses to a few KB and uses almost no distinct colours, so both
signals are checked. Works on local files or on hosted URLs - the URL mode is what
you run after uploading, because the backend's stored object is the ground truth.

Usage
  python check_images.py --dir crops
  python check_images.py --dir crops --ext jpg,jpeg,png,webp --json report.json
  python check_images.py --urls urls.txt            # one URL per line
  python check_images.py --urls urls.txt --min-bytes 50000

Exit codes: 0 clean, 1 suspects found.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request

from PIL import Image

DEFAULT_EXTS = "jpg,jpeg,png,webp"


def inspect_bytes(blob: bytes) -> tuple[int, int]:
    im = Image.open(io.BytesIO(blob)).convert("RGB")
    return im.size[0] * im.size[1], len(set(list(im.getdata())[::53]))


def inspect_path(path: str) -> tuple[int, int]:
    blob = open(path, "rb").read()
    return len(blob), inspect_bytes(blob)[1]


def inspect_url(url: str) -> tuple[int, int]:
    req = urllib.request.Request(url, headers={"User-Agent": "auto-list-products/1.0"})
    blob = urllib.request.urlopen(req, timeout=30).read()
    return len(blob), inspect_bytes(blob)[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--dir", help="folder of local images")
    src.add_argument("--urls", help="text file with one image URL per line")
    ap.add_argument("--ext", default=DEFAULT_EXTS,
                    help="comma separated extensions for --dir; the crops folder mixes jpg and png")
    ap.add_argument("--min-bytes", type=int, default=50000, help="smaller than this counts as blank")
    ap.add_argument("--min-colors", type=int, default=200, help="fewer sampled colours counts as blank")
    ap.add_argument("--json", help="write a JSON report here")
    args = ap.parse_args()

    targets: list[tuple[str, str]] = []
    if args.dir:
        exts = tuple("." + e.strip().lower().lstrip(".") for e in args.ext.split(",") if e.strip())
        for root, _, files in os.walk(args.dir):
            for name in sorted(files):
                if name.lower().endswith(exts):
                    targets.append((os.path.join(root, name), "file"))
        targets.sort()
        if not targets:
            print(f"no {exts} images under {args.dir}", file=sys.stderr)
            return 1
    else:
        with open(args.urls, encoding="utf-8") as fh:
            targets = [(line.strip(), "url") for line in fh if line.strip()]

    rows, bad = [], []
    for target, kind in targets:
        try:
            size, colors = inspect_path(target) if kind == "file" else inspect_url(target)
        except Exception as exc:  # network / decode failures are suspects too
            rows.append({"target": target, "error": str(exc)[:160]})
            bad.append(target)
            continue
        flag = size < args.min_bytes or colors < args.min_colors
        rows.append({"target": target, "bytes": size, "colors": colors, "blank": flag})
        if flag:
            bad.append(target)

    for row in rows:
        name = os.path.basename(row["target"]) if "target" in row else row
        if "error" in row:
            print(f"ERR   {name}: {row['error']}")
        else:
            mark = "BLANK" if row["blank"] else "ok   "
            print(f"{mark} {name}  {row['bytes']}B  colors={row['colors']}")

    if args.json:
        json.dump(rows, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\nchecked {len(rows)}, suspects {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
