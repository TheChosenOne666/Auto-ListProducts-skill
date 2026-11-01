#!/usr/bin/env python3
"""Turn extracted product facts into a resumable listing queue.

products.json (list, one object per product):
  {"n": 1, "name": "示例商品500ml", "size": "500ml", "unit": "瓶", "per": 15,
   "cu": "箱", "supply": 31.5, "retail": 3.0, "cat": "TEA", "img": "示例商品500ml.jpg"}

categories.json (map of category key -> platform path):
  {"TEA": {"l2": "<level-2 label>", "l3": "<level-3 label>"}}

Output queue.json entries carry everything the browser step needs:
  v1/v2 (spec values), price1/pt1 (single unit), price2/pt2 (full case),
  expectCat (exact path string used for the post-selection assertion).

Usage
  python make_queue.py --products products.json --categories categories.json \
      --root "根类目名" --out queue.json [--min-coupon 0.5] [--points-as-coupon]
      [--display-counters] [--no-case-sku] [--only 5,7,8]

Policy notes
  * `--min-coupon` is opt-in: default 0 means nothing is skipped. Set it only when the
    operator has such a rule.
  * points are emitted only when supplied: per-product `"pt1"` / `"pt2"`, or with
    `--points-as-coupon` to derive them from the coupon.
  * the case SKU row is emitted by default; `--no-case-sku` keeps single-unit only.
  * display sales/favourite counters are opt-in via `--display-counters` and are deterministic,
    so reruns stay idempotent.

Exit codes: 0 ok, 1 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--products", required=True)
    ap.add_argument("--categories", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", required=True, help="top-level category name exactly as the cascader shows it")
    ap.add_argument("--min-coupon", type=float, default=0.0,
                    help="opt-in: skip products whose coupon is below this (0 = never skip)")
    ap.add_argument("--only", help="comma separated product numbers to include")
    ap.add_argument("--no-case-sku", action="store_true", help="emit only the single-unit SKU row")
    ap.add_argument("--points-as-coupon", action="store_true",
                    help="derive points from the coupon (one decimal); otherwise only per-product pt1/pt2 are used")
    ap.add_argument("--display-counters", action="store_true",
                    help="also emit pseudo-random display sales/favourite numbers (opt-in)")
    args = ap.parse_args()

    products = json.load(open(args.products, encoding="utf-8"))
    cats = json.load(open(args.categories, encoding="utf-8"))
    only = {int(x) for x in args.only.split(",")} if args.only else None

    queue, skipped, unknown = [], [], []
    for p in products:
        if only and p.get("n") not in only:
            continue
        if p.get("skip"):
            skipped.append((p.get("n"), p.get("name"), "flag"))
            continue
        cat = cats.get(p.get("cat"))
        if not cat:
            unknown.append((p.get("n"), p.get("name"), p.get("cat")))
            continue
        per, supply, retail = int(p["per"]), float(p["supply"]), float(p["retail"])
        coupon = round(retail - supply / per, 2)
        if args.min_coupon > 0 and coupon < args.min_coupon:
            skipped.append((p.get("n"), p.get("name"), coupon))
            continue
        unit, size = p["unit"], p["size"]
        v1 = size if size.endswith(unit) else f"{size}*1{unit}"
        item = {
            "n": p["n"],
            "name": p["name"],
            "img": p["img"],
            "l2": cat["l2"],
            "l3": cat["l3"],
            "expectCat": f"{args.root} / {cat['l2']} / {cat['l3']}",
            "v1": v1,
            "price1": retail,
            "coupon": coupon,
        }
        if "pt1" in p:
            item["pt1"] = p["pt1"]
        elif args.points_as_coupon:
            item["pt1"] = round(coupon, 1)
        if not args.no_case_sku:
            item.update({"v2": f"{per}{unit}*箱", "price2": round(retail * per, 2)})
            if "pt2" in p:
                item["pt2"] = p["pt2"]
            elif args.points_as_coupon:
                item["pt2"] = round(coupon * per, 1)
        if args.display_counters:
            item["sales"] = p.get("sales", 100 + (int(p["n"]) * 137) % 900)
            item["fav"] = p.get("fav", 100 + (int(p["n"]) * 271) % 900)
        queue.append(item)

    json.dump(queue, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"queue {len(queue)} -> {args.out}")
    by_cat: dict[str, int] = {}
    for q in queue:
        key = f"{q['l2']}/{q['l3']}"
        by_cat[key] = by_cat.get(key, 0) + 1
    for key, count in sorted(by_cat.items()):
        print(f"  {key}: {count}")
    if skipped:
        print(f"skipped {len(skipped)}:")
        for item in skipped:
            print("  -", item)
    if unknown:
        print("unknown category keys:", unknown)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
