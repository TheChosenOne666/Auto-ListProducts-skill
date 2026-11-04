#!/usr/bin/env python3
"""Turn extracted product facts into a resumable listing queue.

Every business behaviour (root category, case-pack label, points policy, coupon floor,
display counters, stock, spec name, path separator) comes from `profile.json` written by
the probe phase. Command-line flags exist only to override the profile for one run.

products.json (list, one object per product):
  {"n": 1, "name": "示例商品500ml", "size": "500ml", "unit": "瓶", "per": 15,
   "cu": "箱", "supply": 31.5, "retail": 3.0, "cat": "TEA", "img": "示例商品500ml.jpg"}
  optional per-product overrides: "stock", "pt1", "pt2", "sales", "fav", "skip": true

categories.json (map of category key -> platform path):
  {"TEA": {"l2": "<level-2 label>", "l3": "<level-3 label>"}}

Output queue.json entries carry everything the browser step needs for one item:
  v1/v2 (spec values), price1/pt1 (single unit), price2/pt2 (full case), stock, specName,
  expectCat (the exact path string used for the post-selection assertion).

Usage
  python make_queue.py --profile profile.json --products products.json \
      --categories categories.json --out queue.json

Overrides (each wins over the profile)
  --root "根类目名"        --min-coupon 0.5        --points coupon|fixed|none
  --display-counters      --no-case-sku           --only 5,7,8
  --stock 10000           --spec-name 规格

Policy notes
  * Nothing is skipped by default: `business.minCoupon` of 0 means no coupon floor.
  * points are emitted per `business.pointsMode`: `none` (omit), `coupon` (derive, one
    decimal by default), `fixed` (only per-product pt1/pt2, and a product missing them is
    an error rather than a silently blank field).
  * the case SKU row is emitted unless `--no-case-sku` / `business.caseSku` is false.
  * display counters are opt-in and deterministic, so reruns stay idempotent.
  * same name + same retail is a duplicate (dropped); same name + different retail is kept
    but reported, because those are often genuinely different listings.
  * a coupon that is zero, negative, or the whole retail price stops the run: the numbers
    are wrong, not the product.
  * `"cu"` (case-pack unit word) is required only when the profile's caseSkuLabel uses `{cu}`.

Exit codes: 0 ok, 1 usage or data error.
"""
from __future__ import annotations

import argparse
import json
import sys

REQUIRED_KEYS = ["n", "name", "size", "unit", "per", "supply", "retail", "cat", "img"]


def load(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SystemExit(f"no such file: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}")


def case_label(template: str, per: int, unit: str, cu: str | None) -> str:
    values = {"per": per, "unit": unit}
    if cu:
        values["cu"] = cu
    return template.format(**values)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, help="profile.json from the probe phase")
    ap.add_argument("--products", required=True)
    ap.add_argument("--categories", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", help="override defaults.rootCategory")
    ap.add_argument("--min-coupon", type=float, help="override business.minCoupon (0 = never skip)")
    ap.add_argument("--points", choices=("none", "coupon", "fixed"), help="override business.pointsMode")
    ap.add_argument("--display-counters", action="store_true", help="force display sales/favourite counters on")
    ap.add_argument("--no-case-sku", action="store_true", help="emit only the single-unit SKU row")
    ap.add_argument("--only", help="comma separated product numbers to include")
    ap.add_argument("--stock", type=int, help="override defaults.stock")
    ap.add_argument("--spec-name", help="override defaults.specName")
    args = ap.parse_args()

    profile = load(args.profile)
    products = load(args.products)
    cats = load(args.categories)

    business = profile.get("business", {})
    defaults = profile.get("defaults", {})
    category_cfg = profile.get("fields", {}).get("category", {})
    spec_cfg = profile.get("fields", {}).get("spec", {})

    root = args.root or defaults.get("rootCategory")
    if not root:
        print("no root category: set defaults.rootCategory in the profile or pass --root", file=sys.stderr)
        return 1
    separator = category_cfg.get("expectedSeparator") or " / "
    case_template = spec_cfg.get("caseSkuLabel") or "{per}{unit}*{cu}"
    points_mode = args.points or business.get("pointsMode") or "none"
    decimals = profile.get("fields", {}).get("optional", {}).get("points", {}).get("decimals", 1)
    min_coupon = args.min_coupon if args.min_coupon is not None else (business.get("minCoupon") or 0.0)
    counters = True if args.display_counters else bool(business.get("counters", False))
    case_sku = False if args.no_case_sku else bool(business.get("caseSku", True))
    stock = args.stock if args.stock is not None else defaults.get("stock")
    spec_name = args.spec_name or defaults.get("specName")
    only = {int(x) for x in args.only.split(",")} if args.only else None

    queue, skipped, unknown, problems, suspects, dupes, clashes, seen = [], [], [], [], [], [], [], {}

    for p in products:
        missing = [k for k in REQUIRED_KEYS if p.get(k) in (None, "")]
        if missing:
            problems.append((p.get("n"), p.get("name"), f"missing {missing}"))
            continue
        if only and p["n"] not in only:
            continue
        if p.get("skip"):
            skipped.append((p["n"], p["name"], "flag"))
            continue
        cat = cats.get(p["cat"])
        if not cat or not cat.get("l2") or not cat.get("l3"):
            unknown.append((p["n"], p["name"], p["cat"]))
            continue

        try:
            per, supply, retail = int(p["per"]), float(p["supply"]), float(p["retail"])
        except (TypeError, ValueError) as exc:
            problems.append((p["n"], p["name"], f"bad number ({exc})"))
            continue
        if per < 1:
            problems.append((p["n"], p["name"], f"`per` (units per case) must be >= 1, got {per}"))
            continue

        coupon = round(retail - supply / per, 2)
        if coupon <= 0 or coupon >= retail:
            suspects.append((p["n"], p["name"], f"coupon={coupon} retail={retail} supply={supply} per={per}"))
            continue
        if min_coupon > 0 and coupon < min_coupon:
            skipped.append((p["n"], p["name"], coupon))
            continue

        unit, size, cu = p["unit"], p["size"], p.get("cu")
        dup_key = (str(p["name"]).strip(), retail)
        if dup_key in seen:
            dupes.append((p["n"], p["name"], f"same name and retail as n={seen[dup_key]}"))
            continue
        same_name = sorted({n for (name, _), n in seen.items() if name == str(p["name"]).strip()})
        if same_name:
            clashes.append((p["n"], p["name"], same_name))
        seen[dup_key] = p["n"]

        v1 = size if size.endswith(unit) else f"{size}*1{unit}"
        item = {
            "n": p["n"],
            "name": p["name"],
            "img": p["img"],
            "l2": cat["l2"],
            "l3": cat["l3"],
            "expectCat": separator.join([root, cat["l2"], cat["l3"]]),
            "v1": v1,
            "price1": retail,
            "coupon": coupon,
        }
        if spec_name:
            item["specName"] = spec_name
        if p.get("stock") is not None:
            item["stock"] = int(p["stock"])
        elif stock is not None:
            item["stock"] = int(stock)

        if points_mode == "coupon":
            item["pt1"] = round(coupon, decimals)
        elif points_mode == "fixed":
            if p.get("pt1") is None:
                problems.append((p["n"], p["name"], "pointsMode=fixed but no pt1"))
                continue
            item["pt1"] = p["pt1"]

        if case_sku:
            try:
                item["v2"] = case_label(case_template, per, unit, cu)
            except KeyError as exc:
                problems.append((p["n"], p["name"], f"caseSkuLabel needs {exc} but the product has no such value"))
                continue
            item["price2"] = round(retail * per, 2)
            if points_mode == "coupon":
                item["pt2"] = round(coupon * per, decimals)
            elif points_mode == "fixed":
                if p.get("pt2") is None:
                    problems.append((p["n"], p["name"], "pointsMode=fixed but no pt2"))
                    continue
                item["pt2"] = p["pt2"]

        if counters:
            item["sales"] = p.get("sales", 100 + (int(p["n"]) * 137) % 900)
            item["fav"] = p.get("fav", 100 + (int(p["n"]) * 271) % 900)

        queue.append(item)

    if problems or unknown or suspects:
        for n, name, why in problems:
            print(f"BAD      {n} {name}: {why}")
        for n, name, key in unknown:
            print(f"NO-CATEGORY {n} {name}: key {key!r} not in categories.json")
        for n, name, why in suspects:
            print(f"SUSPECT  {n} {name}: {why}")
        print("\nfix the numbers or the category map, then rebuild the queue")
        return 1

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(queue, fh, ensure_ascii=False, indent=1)

    print(f"queue {len(queue)} -> {args.out}")
    print(f"profile: root={root} points={points_mode} minCoupon={min_coupon} "
          f"counters={counters} caseSku={case_sku} stock={stock if stock is not None else '-'}")
    by_cat: dict[str, int] = {}
    for q in queue:
        key = f"{q['l2']}/{q['l3']}"
        by_cat[key] = by_cat.get(key, 0) + 1
    for key, count in sorted(by_cat.items()):
        print(f"  {key}: {count}")
    if skipped:
        print(f"skipped {len(skipped)}:")
        for entry in skipped:
            print("  -", entry)
    if dupes:
        print(f"duplicates dropped {len(dupes)}:")
        for n, name, why in dupes:
            print(f"  - {n} {name}: {why}")
    if clashes:
        print("same name, different price (kept - confirm each is a real listing):")
        for n, name, prior in clashes:
            print(f"  - {n} {name} also on n={prior}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
