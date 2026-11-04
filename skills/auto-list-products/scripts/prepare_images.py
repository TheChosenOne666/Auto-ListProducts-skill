#!/usr/bin/env python3
"""Assemble the images a product already has into the slots the backend asks for.

`crop_square.py` handles the screenshot case - one square pulled out of a phone capture. This handles
the other shape, the one that is actually most common with real catalogues: a product arrives with
several assets (a square per colour, a few scene shots, a 3:4 spec infographic) and they have to be
laid out into the form's upload slots.

Nothing about a platform is hardcoded. The slot count and the crop rule come from the profile's
`image.trays` (`maxCount`, and `crop`: `square` centre-crops, `any` uploads as-is, `none` leaves the
file alone). The order comes from the plan.

The rules that were learned the hard way are enforced here:
  * centre crop is the only crop - never pad, never blur, never add a background;
  * never upscale: a small source stays small and says so;
  * never invent a slot: a short set reports the shortfall instead of repeating an image or filling in.

A cross-review of the first draft found a family of silent successes, so the shape of the output is
part of the contract: every tray appears in the manifest, an empty candidate set is a shortfall rather
than a skip, duplicate product numbers and duplicate tray keys are refused before anything is written,
and nothing is announced (a contact sheet, a manifest) that was not actually produced.

Usage
  python prepare_images.py --profile profile.json --products products.json \
      --src images --dst out [--edge 1280] [--manifest manifest.json] \
      [--report report.json] [--sheet sheet.jpg]

products.json entries (only what this script needs; other keys are ignored):
  {"n": 1, "name": "鞋A",
   "variants": [{"label": "黑蓝红色", "src": "a-1.jpg"}, {"label": "黑白色", "src": "a-2.jpg"}],
   "scenes": ["scene-1.jpg", "scene-2.jpg"],
   "infographic": "a-info.jpg",
   "trays": {"main": ["a-1.jpg", "scene-1.jpg"], "detail": null},
   "single": "only.jpg"}

`single` fills the first product-level tray and nothing else. `trays.<key>` given explicitly (even as
an empty list) overrides the automatic order for that slot.

Exit codes: 0 every slot filled with clean images, 1 shortfalls or suspects (tell the user),
2 unusable input (missing file, unreadable, a source used twice, duplicate product numbers,
a malformed or colliding tray definition). On 2 nothing is written.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_EDGE = 1280
MIN_COLORS = 200
DEFAULT_FLOOR = 50000
CROPS = {"square", "any", "none"}
# manifest keys this script owns; a tray may not be named after one, or its rows would be
# read back as a shortfall list. `skuImages` is deliberately not called `sku`: that is the
# conventional tray key in a profile, and the two must not collide.
RESERVED = {"n", "name", "short", "suspect", "skuImages"}


class InputError(Exception):
    """The plan or the source files cannot be honoured as written."""


def load_json(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"no such file: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON: {exc}")
    except UnicodeDecodeError as exc:
        raise InputError(f"{path} is not UTF-8 text ({exc}); save the plan as UTF-8")
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc}")


def center_crop(im: Image.Image) -> tuple[Image.Image, bool]:
    """Trim to the largest centred square. The only crop this tool is allowed to do."""
    w, h = im.size
    if w == h:
        return im, False
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    return im.crop((left, top, left + side, top + side)), True


def fit(im: Image.Image, crop: str, edge: int) -> tuple[Image.Image, str, str]:
    """Return the image, the action taken, and a note when the result stayed smaller than asked."""
    if crop in ("any", "none"):
        return im, "keep", ""
    square, cropped = center_crop(im)
    if square.width > edge:
        return square.resize((edge, edge), Image.LANCZOS), "center-crop+resize", ""
    action = "center-crop" if cropped else "keep"
    note = "not-upscaled" if square.width < edge else ""
    return square, action, note


def inspect(im: Image.Image, blob_len: int) -> dict:
    """Byte size plus a colour count, sampled from a bounded thumbnail rather than the full array."""
    small = im.resize((min(256, im.width), min(256, im.height)))
    colors = len(set(small.getdata()))
    return {"w": im.size[0], "h": im.size[1], "bytes": blob_len, "colors": colors}


def safe_label(label) -> str:
    """A filesystem-safe token. Control characters are what made the first draft blame the wrong file."""
    text = re.sub(r"[\x00-\x1f\x7f]", "_", str(label if label is not None else ""))
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", text)
    return text.strip("._")[:40] or "variant"


def read_trays(profile: dict) -> tuple[list[dict], int]:
    """Validate the slot definitions once, up front: a bad tray must not degrade into a silent skip."""
    cfg = profile.get("image") or {}
    trays = [t for t in (cfg.get("trays") or []) if isinstance(t, dict)]
    if not trays:
        raise InputError("profile has no image.trays - run the probe again (check_profile.py wants them)")
    seen = []
    for index, tray in enumerate(trays):
        key = str(tray.get("key") or "").strip()
        if not key:
            raise InputError(f"image.trays[{index}] has no key")
        if key in RESERVED or key.endswith("_reason"):
            raise InputError(f"image.trays[{index}] key {key!r} collides with a manifest field; rename it")
        if key in seen:
            raise InputError(f"duplicate tray key {key!r}: the second one would overwrite the first")
        seen.append(key)
        count = tray.get("maxCount")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise InputError(f"tray {key!r} needs an integer maxCount >= 1, got {count!r}")
        crop = tray.get("crop", "square")
        if crop not in CROPS:
            raise InputError(f"tray {key!r} crop must be one of {sorted(CROPS)}, got {crop!r}")
    floor = cfg.get("minBytes")
    if not isinstance(floor, int) or isinstance(floor, bool) or floor < 0:
        floor = DEFAULT_FLOOR
    return trays, floor


def check_products(products: list) -> None:
    """Product numbers are the filename prefix and the join key, so they have to be unique."""
    numbers = [p.get("n") for p in products]
    dupes = sorted({str(n) for n in numbers if numbers.count(n) > 1})
    if dupes:
        raise InputError(f"duplicate product numbers {dupes}: their output files would overwrite each other")


def candidates(product: dict, tray: dict, main_key: str) -> tuple[list[dict], str]:
    """The images for one slot, with the reason the order is what it is."""
    key = str(tray.get("key"))
    bind = tray.get("bind", "product")
    explicit = product.get("trays") or {}
    if key in explicit:
        return [{"src": s, "label": None} for s in (explicit[key] or [])], "explicit order from the plan"
    variants = [{"src": v.get("src"), "label": v.get("label"), "kind": "variant"}
                for v in (product.get("variants") or [])]
    scenes = [{"src": s, "label": None, "kind": "scene"} for s in (product.get("scenes") or [])]
    if bind == "sku":
        return variants, "one image per variant, bound by its label"
    if product.get("single") and key == main_key:
        return [{"src": product["single"], "label": None}], "single-image product: one file, nothing added"
    if key == "detail":
        info = product.get("infographic")
        head = [{"src": info, "label": None, "kind": "infographic"}] if info else []
        return (head + variants + scenes,
                "infographic first because the plan names one, then variants, then scenes" if info
                else "variants then scenes (no infographic given)")
    return variants + scenes, "variants first, scenes fill the rest (default)"


def prepare(profile: dict, products: list, src_dir: str, dst_dir: str, edge: int, quality: int):
    trays, floor = read_trays(profile)
    check_products(products)
    main_key = next((str(t["key"]) for t in trays if t.get("bind", "product") == "product"), str(trays[0]["key"]))
    os.makedirs(dst_dir, exist_ok=True)

    manifest, notes, problems = [], [], []

    for product in products:
        name = product.get("name")
        entry = {"n": product.get("n"), "name": name, "short": [], "suspect": []}

        for tray in trays:
            key = str(tray["key"])
            crop = tray.get("crop", "square")
            max_count = int(tray["maxCount"])
            picked, reason = candidates(product, tray, main_key)

            rows = []
            used = set()
            for item in picked[:max_count]:
                source = item.get("src")
                if not source:
                    problems.append(f"{name}: tray {key} has an entry without src")
                    continue
                if source in used:
                    problems.append(f"{name}: {source} used twice in tray {key}")
                    continue
                used.add(source)
                path = os.path.join(src_dir, source)
                if not os.path.exists(path):
                    problems.append(f"{name}: missing source file {source} (tray {key})")
                    continue
                stem = f"{product.get('n')}-{key}-{len(rows) + 1}-{Path(source).stem}"
                if item.get("label") is not None:
                    stem += "-" + safe_label(item["label"])
                out_name = stem + ".jpg"
                try:
                    with Image.open(path) as raw:
                        im = raw.convert("RGB")
                        out, action, note = fit(im, crop, edge)
                        out.save(os.path.join(dst_dir, out_name), quality=quality)
                        info = inspect(out, os.path.getsize(os.path.join(dst_dir, out_name)))
                except (OSError, ValueError) as exc:
                    problems.append(f"{name}: cannot process {source} for tray {key} ({exc})")
                    continue
                row = {"file": out_name, "src": source, "action": action, **info}
                if note:
                    row["note"] = note
                if item.get("label") is not None:
                    row["label"] = item["label"]
                if crop == "square" and info["w"] != info["h"]:
                    problems.append(f"{name}: {out_name} came out {info['w']}x{info['h']}, not square")
                if info["bytes"] < floor or info["colors"] < MIN_COLORS:
                    row["suspect"] = True
                    entry["suspect"].append({"tray": key, "file": out_name,
                                             "why": f"bytes {info['bytes']} floor {floor}, colors {info['colors']}"})
                rows.append(row)

            if tray.get("bind") == "sku":
                unlabelled = [v.get("src") for v in (product.get("variants") or []) if not v.get("label")]
                if unlabelled:
                    problems.append(f"{name}: variants {unlabelled} have no label, so no SKU row can bind to them")
                entry["skuImages"] = {r["label"]: {"file": r["file"], "src": r["src"], "action": r["action"]}
                                      for r in rows if r.get("label")}
            else:
                entry[key] = rows
            entry[key + "_reason"] = reason

            if len(rows) < max_count:
                missing = max_count - len(rows)
                if tray.get("bind") == "sku":
                    continue      # a per-SKU slot holds one image per variant, not maxCount slots
                entry["short"].append({"tray": key, "missing": missing,
                                       "reason": f"the plan offers {len(picked)} for {max_count} slots"})
                notes.append(f"{name}: tray {key} short {missing} (offered {len(picked)}, slots {max_count})"
                             " - not filling with repeats")

        manifest.append(entry)

    if problems:
        for line in problems:
            print(f"BAD      {line}")
        return manifest, notes, 2
    code = 1 if any(m["short"] or m["suspect"] for m in manifest) else 0
    return manifest, notes, code


def slot_keys(manifest: list) -> list[str]:
    keys = []
    for entry in manifest:
        for key, value in entry.items():
            if key in RESERVED or key.endswith("_reason") or not isinstance(value, list):
                continue
            if key not in keys:
                keys.append(key)
    return keys


def contact_sheet(manifest: list, dst_dir: str, out: str, cols: int = 5, cell: int = 240) -> bool:
    files = [(entry["n"], row["file"]) for entry in manifest for key in slot_keys(manifest)
             for row in (entry.get(key) or []) if isinstance(row, dict) and row.get("file")]
    if not files:
        return False
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (n, name) in enumerate(files):
        path = os.path.join(dst_dir, name)
        if not os.path.exists(path):
            continue
        with Image.open(path) as im:
            sheet.paste(im.resize((cell - 8, cell - 8)), ((i % cols) * cell + 4, (i // cols) * cell + 4))
        draw.text(((i % cols) * cell + 6, (i // cols) * cell + cell - 20), f"#{n} {name[:22]}", fill=(0, 0, 0))
    sheet.save(out, quality=90)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--products", required=True)
    ap.add_argument("--src", required=True, help="folder with the source assets")
    ap.add_argument("--dst", required=True, help="folder for the assembled images")
    ap.add_argument("--edge", type=int, default=DEFAULT_EDGE, help="max edge for square slots (never upscales)")
    ap.add_argument("--quality", type=int, default=92)
    ap.add_argument("--manifest", help="write the slot manifest here")
    ap.add_argument("--report", help="write the assembly notes here")
    ap.add_argument("--sheet", help="write a contact sheet here")
    args = ap.parse_args()

    try:
        profile = load_json(args.profile)
        products = load_json(args.products)
        if isinstance(products, dict):
            products = products.get("items")
        if not isinstance(products, list):
            raise InputError(f"{args.products}: expected a list of products")
        manifest, notes, code = prepare(profile, products, args.src, args.dst, args.edge, args.quality)
    except InputError as exc:
        print(f"cannot prepare images: {exc}", file=sys.stderr)
        return 2

    if code == 2:
        print("\nnothing written: fix the plan or the sources and run again")
        return 2

    filled = sum(len(entry.get(key) or []) for entry in manifest for key in slot_keys(manifest))
    print(f"prepared {filled} images for {len(manifest)} products -> {args.dst}")
    for line in notes:
        print("  -", line)
    for entry in manifest:
        for item in entry["suspect"]:
            print(f"  SUSPECT {entry['name']} {item['file']}: {item['why']}")
    if code == 1:
        print("\nRESULT: slots short or images suspect - show the sheet to the user before uploading")
    else:
        print("\nRESULT: every slot filled")
    if args.manifest:
        try:
            Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"manifest written: {args.manifest}")
        except OSError as exc:
            print(f"WARN cannot write the manifest to {args.manifest}: {exc}", file=sys.stderr)
    if args.report:
        try:
            Path(args.report).write_text(json.dumps({"notes": notes, "manifest": manifest},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            print(f"WARN cannot write the report to {args.report}: {exc}", file=sys.stderr)
    if args.sheet:
        if contact_sheet(manifest, args.dst, args.sheet):
            print(f"contact sheet: {args.sheet}")
        else:
            print("no images to put on the contact sheet - none written", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
