#!/usr/bin/env python3
"""Turn extracted product facts into a resumable listing queue.

Every business behaviour (root category, case-pack label, points policy, coupon floor,
display counters, stock, spec name, path separator) comes from `profile.json` written by
the probe phase. Command-line flags exist only to override the profile for one run.

products.json (list, one object per product):
  {"n": 1, "name": "示例商品500ml", "size": "500ml", "unit": "瓶", "per": 15,
   "cu": "箱", "supply": 31.5, "retail": 3.0, "cat": "TEA", "img": "示例商品500ml.jpg"}
  optional per-product overrides: "stock", "pt1", "pt2", "sales", "fav", "skip": true

categories.json (map of category key -> platform path; l3 is optional because real trees are not
uniformly three levels deep - 酒水饮料 / 汽水 is a valid leaf - and l1 overrides the profile's root
when the catalogue's first level varies rather than sitting under one fixed root):
  {"TEA": {"l2": "<level-2 label>", "l3": "<level-3 label>"},
   "SODA": {"l2": "<level-2 label>"},
   "NOODLE": {"l1": "<level-1 label>", "l2": "<level-2 label>"}}

Output queue.json entries carry everything the browser step needs for one item:
  v1/v2 (spec values), price1/pt1 (single unit), price2/pt2 (full case), stock, specName,
  expectCat (the exact path string used for the post-selection assertion), and board when tagged.

Usage
  python make_queue.py --profile profile.json --products products.json \
      --categories categories.json --out queue.json

Overrides (each wins over the profile)
  --root "根类目名"        --min-coupon 0.5        --points coupon|fixed|none
  --display-counters      --no-case-sku           --only 5,7,8
  --stock 10000           --spec-name 规格         --board pickup

--board is stronger than an override: when the profile declares `boards`, picking one merges that
section's form URLs, root category, field set, SKU column order, pre-checked controls and
`couponOffset` over the top level, and tags every item. Retail prices never move with a board -
only the coupon and what derives from it.

Policy notes
  * `supply` and `per` are required only when something consumes them: a case SKU row, coupon-derived
    points, or a coupon floor. A retail-only dictated batch still builds - it just carries no coupon.
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

# a queue is built from the user's own names and labels, and a Windows console opens on a
# legacy codepage that raises UnicodeEncodeError halfway through printing them
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REQUIRED_KEYS = ["n", "name", "size", "unit", "retail", "cat", "img"]
COST_KEYS = ["per", "supply"]


def load(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SystemExit(f"no such file: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}")
    except UnicodeDecodeError as exc:
        raise SystemExit(f"{path} is not UTF-8 text ({exc}); save the manifest as UTF-8")
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}")


def case_label(template: str, per: int, unit: str, cu: str | None) -> str:
    values = {"per": per, "unit": unit}
    if cu:
        values["cu"] = cu
    return template.format(**values)


class ProfileError(Exception):
    """The profile cannot answer what was asked of it - an input problem, not a data disagreement."""


def board_view(profile: dict, board: str | None) -> dict:
    """Merge one section's overrides over the top level; a single-section profile is unaffected."""
    view = {"label": None,
            "form": dict(profile.get("form") or {}),
            "defaults": dict(profile.get("defaults") or {}),
            "fields": dict(profile.get("fields") or {}),
            "business": dict(profile.get("business") or {}),
            "without": []}
    if not board:
        return view
    boards = {k: v for k, v in (profile.get("boards") or {}).items() if isinstance(v, dict)}
    if board not in boards:
        raise ProfileError(f"unknown --board {board!r}; this profile defines: {sorted(boards) or 'none'}")
    over = boards[board]
    view["label"] = over.get("label")
    for section in ("form", "defaults", "business"):
        view[section].update(over.get(section) or {})
    fields = over.get("fields") or {}
    view["without"] = list(fields.get("without") or [])
    for name, value in fields.items():
        if name == "without":
            continue
        if isinstance(value, dict) and isinstance(view["fields"].get(name), dict):
            merged = dict(view["fields"][name])
            merged.update(value)
            view["fields"][name] = merged
        else:
            view["fields"][name] = value
    for name in view["without"]:
        view["fields"].pop(name, None)
    return view


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
    ap.add_argument("--board", help="tag every item with a board key (pickup, express) so the audit can "
                                    "reconcile each section of the shop on its own")
    args = ap.parse_args()

    profile = load(args.profile)
    products = load(args.products)
    cats = load(args.categories)

    try:
        view = board_view(profile, args.board)
    except ProfileError as exc:
        print(f"cannot build the queue: {exc}", file=sys.stderr)
        return 2
    business = view["business"]
    defaults = view["defaults"]
    category_cfg = view["fields"].get("category", {})
    spec_cfg = view["fields"].get("spec", {})
    coupon_offset = business.get("couponOffset") or 0

    root = args.root or defaults.get("rootCategory")
    if not root:
        print("no root category: set defaults.rootCategory in the profile or pass --root", file=sys.stderr)
        return 1
    separator = category_cfg.get("expectedSeparator") or " / "
    case_template = spec_cfg.get("caseSkuLabel") or "{per}{unit}*{cu}"
    points_mode = args.points or business.get("pointsMode") or "none"
    decimals = view["fields"].get("optional", {}).get("points", {}).get("decimals", 1)
    min_coupon = args.min_coupon if args.min_coupon is not None else (business.get("minCoupon") or 0.0)
    counters = True if args.display_counters else bool(business.get("counters", False))
    case_sku = False if args.no_case_sku else bool(business.get("caseSku", True))
    stock = args.stock if args.stock is not None else defaults.get("stock")
    spec_name = args.spec_name or defaults.get("specName")
    only = {int(x) for x in args.only.split(",")} if args.only else None

    queue, skipped, unknown, problems, suspects, dupes, clashes, seen = [], [], [], [], [], [], [], {}

    needs_cost = case_sku or points_mode == "coupon" or min_coupon > 0

    for p in products:
        required = REQUIRED_KEYS + (COST_KEYS if needs_cost else [])
        missing = [k for k in required if p.get(k) in (None, "")]
        if missing:
            problems.append((p.get("n"), p.get("name"), f"missing {missing}"
                             + (" (supply/per are only needed for a case row, coupon-derived points or a coupon floor)"
                                if any(k in COST_KEYS for k in missing) else "")))
            continue
        if only and p["n"] not in only:
            continue
        if p.get("skip"):
            skipped.append((p["n"], p["name"], "flag"))
            continue
        cat = cats.get(p["cat"])
        if not cat or not cat.get("l2"):
            unknown.append((p["n"], p["name"], p["cat"]))
            continue
        # some trees are not three levels deep everywhere: 酒水饮料 / 汽水 is a valid leaf,
        # and a catalogue whose first level varies overrides the profile's root per key with l1
        levels = [cat.get("l1") or root, cat["l2"]] + ([cat["l3"]] if cat.get("l3") else [])

        try:
            per = int(p["per"]) if p.get("per") not in (None, "") else None
            supply = float(p["supply"]) if p.get("supply") not in (None, "") else None
            retail = float(p["retail"])
        except (TypeError, ValueError) as exc:
            problems.append((p["n"], p["name"], f"bad number ({exc})"))
            continue
        if per is not None and per < 1:
            problems.append((p["n"], p["name"], f"`per` (units per case) must be >= 1, got {per}"))
            continue

        base_coupon = round(retail - supply / per, 2) if (supply is not None and per) else None
        coupon = round(base_coupon + coupon_offset, 2) if base_coupon is not None else None
        if coupon is not None and (coupon <= 0 or coupon >= retail):
            suspects.append((p["n"], p["name"], f"coupon={coupon} (base {base_coupon} "
                                                f"offset {coupon_offset:+g}) retail={retail} supply={supply} per={per}"))
            continue
        if coupon is not None and min_coupon > 0 and coupon < min_coupon:
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
            "expectCat": separator.join(levels),
            "v1": v1,
            "price1": retail,
        }
        if cat.get("l3"):
            item["l3"] = cat["l3"]
        if cat.get("l1"):
            item["l1"] = cat["l1"]
        if coupon is not None:
            item["coupon"] = coupon
        if args.board:
            item["board"] = args.board
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
    section = f" board={args.board} ({view['label']})" if args.board else ""
    print(f"profile{section}: root={root} points={points_mode} minCoupon={min_coupon} "
          f"counters={counters} caseSku={case_sku} stock={stock if stock is not None else '-'} "
          f"couponOffset={coupon_offset:+g}")
    by_cat: dict[str, int] = {}
    for q in queue:
        key = "/".join([p for p in (q.get("l1"), q["l2"], q.get("l3")) if p])
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
