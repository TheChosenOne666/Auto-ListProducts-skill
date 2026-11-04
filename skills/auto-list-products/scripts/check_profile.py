#!/usr/bin/env python3
"""Validate a platform profile produced by the probe phase.

The probe writes `profile.json` by inspecting the user's admin form. This script
refuses a half-probed profile before any product is touched, and prints a short
human summary the user can confirm.

Usage
  python scripts/check_profile.py profile.json
  python scripts/check_profile.py profile.json --quiet

Exit codes: 0 ok, 1 missing/invalid fields, 2 unreadable file.
"""
from __future__ import annotations

import argparse
import json
import sys

# product names, tray labels and notes are the user's data, and a Windows console opens on
# a legacy codepage that raises UnicodeEncodeError halfway through a batch
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REQUIRED = [
    "platform",
    "image.trays",
    "fields.name.locator",
    "fields.category.kind",
    "fields.category.locator",
    "fields.category.expectedSeparator",
    "fields.spec.nameLocator",
    "fields.spec.valueLocator",
    "fields.spec.skuInputsLocator",
    "fields.submit.button",
    "defaults.rootCategory",
    "business.mode",
]
# form.newUrl / form.listUrl are required at the top level OR in every board - see check_boards()
BOARD_SECTIONS = {"label", "form", "defaults", "fields", "business"}
BOARD_FIELD_KEYS = {"without", "spec", "name", "category", "scope", "optional", "submit"}
BOARD_DEFAULT_KEYS = {"stock", "specName", "rootCategory", "checkedToStart"}
BOARD_BUSINESS_KEYS = {"mode", "pointsMode", "minCoupon", "counters", "caseSku", "couponOffset"}
LOCATOR_PATHS = [
    "fields.name.locator",
    "fields.category.locator",
    "fields.category.panelLocator",
    "fields.scope.locator",
    "fields.spec.nameLocator",
    "fields.spec.valueLocator",
    "fields.spec.addValueButton",
    "fields.spec.skuInputsLocator",
    "fields.submit.button",
    "fields.submit.confirmButton",
]
TRAY_LOCATORS = ["uploadTrigger", "thumbLocator", "removeLocator"]
TRAY_CROPS = {"square", "any", "none"}
TRAY_BINDS = {"product", "sku"}
MODES = {"fill-only", "fill-and-submit"}
POINTS_MODES = {"none", "coupon", "fixed"}


def get(doc, path, default=None):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return default if cur is None else cur


def check_locator(doc, path, errors):
    loc = get(doc, path)
    if loc is None:
        return
    if not isinstance(loc, dict) or not loc.get("by") or not loc.get("value"):
        errors.append(f"bad locator: {path} (want {{'by': ..., 'value': ...}})")


def check_trays(doc, errors, warnings):
    trays = get(doc, "image.trays")
    if not isinstance(trays, list) or not trays:
        errors.append("image.trays must be a non-empty list (one entry per upload slot on the form)")
        return []
    for i, tray in enumerate(trays):
        tag = f"image.trays[{i}]"
        if not isinstance(tray, dict):
            errors.append(f"{tag}: want an object")
            continue
        key = tray.get("key") or f"{tag} (missing key)"
        for field in TRAY_LOCATORS:
            loc = tray.get(field)
            if not isinstance(loc, dict) or not loc.get("by") or not loc.get("value"):
                errors.append(f"{key}: {field} must be {{'by': ..., 'value': ...}}")
        if not tray.get("label"):
            errors.append(f"{key}: label missing - the fill loop finds this tile by its visible text")
        crop = tray.get("crop", "square")
        if crop not in TRAY_CROPS:
            errors.append(f"{key}: crop must be one of {sorted(TRAY_CROPS)}, got {crop!r}")
        if crop == "square" and not tray.get("cropConfirm"):
            errors.append(f"{key}: crop=square needs cropConfirm (the button that commits the crop dialog)")
        bind = tray.get("bind", "product")
        if bind not in TRAY_BINDS:
            errors.append(f"{key}: bind must be one of {sorted(TRAY_BINDS)}, got {bind!r}")
        if not isinstance(tray.get("maxCount", 1), int) or int(tray.get("maxCount", 1)) < 1:
            errors.append(f"{key}: maxCount must be an integer >= 1")
    keys = [t.get("key") for t in trays if isinstance(t, dict)]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        errors.append(f"duplicate tray keys: {sorted(dupes)}")
    if "product" not in [t.get("bind", "product") for t in trays if isinstance(t, dict)]:
        errors.append("no tray is bound to the product - which slot receives the product image?")
    if not any(t.get("bind") == "sku" for t in trays if isinstance(t, dict)):
        warnings.append("no bind=sku tray: per-variant images will be skipped if the form has them")
    return trays


def check_boards(doc, errors, warnings):
    """Validate the optional per-section overrides and say which URLs come from where."""
    boards = doc.get("boards")
    if boards is None:
        for path in ("form.newUrl", "form.listUrl"):
            if not get(doc, path):
                errors.append(f"missing: {path} (or give every board its own)")
        return {}
    if not isinstance(boards, dict) or not boards:
        errors.append("boards must be a non-empty object keyed by board name")
        return {}
    for key, board in boards.items():
        tag = f"boards.{key}"
        if key.startswith("_"):
            continue          # a comment key is documentation, not a section
        if not isinstance(board, dict):
            errors.append(f"{tag}: want an object")
            continue
        if not board.get("label"):
            errors.append(f"{tag}.label missing - the user has to recognise the section")
        for section in board:
            if section in ("_comment",):
                continue
            if section not in BOARD_SECTIONS:
                errors.append(f"{tag}: unknown section {section!r} (allowed: {sorted(BOARD_SECTIONS)})")
        for section, allowed in (("fields", BOARD_FIELD_KEYS), ("defaults", BOARD_DEFAULT_KEYS),
                                 ("business", BOARD_BUSINESS_KEYS)):
            for name in (board.get(section) or {}):
                if name not in allowed:
                    errors.append(f"{tag}.{section}: unknown override {name!r} (allowed: {sorted(allowed)})")
        form = board.get("form") or {}
        if form and (not form.get("newUrl") or not form.get("listUrl")):
            errors.append(f"{tag}.form needs both newUrl and listUrl")
        offset = (board.get("business") or {}).get("couponOffset")
        if offset is not None and (not isinstance(offset, (int, float)) or isinstance(offset, bool)):
            errors.append(f"{tag}.business.couponOffset must be a number, got {offset!r}")
        without = (board.get("fields") or {}).get("without")
        if without is not None and not isinstance(without, list):
            errors.append(f"{tag}.fields.without must be a list of field names")
    if not any((b.get("form") or {}).get("newUrl") for b in boards.values() if isinstance(b, dict)) \
            and not get(doc, "form.newUrl"):
        errors.append("no form.newUrl anywhere: neither the top level nor any board gives one")
    if not get(doc, "form.newUrl"):
        missing = [k for k, b in boards.items()
                   if isinstance(b, dict) and not (b.get("form") or {}).get("newUrl")]
        if missing:
            errors.append(f"boards without a form.newUrl while the top level has none: {sorted(missing)}")
    return boards


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile")
    ap.add_argument("--quiet", action="store_true", help="only print errors")
    args = ap.parse_args()

    try:
        doc = json.load(open(args.profile, encoding="utf-8"))
    except Exception as exc:
        print(f"cannot read profile: {exc}", file=sys.stderr)
        return 2

    errors, warnings = [], []

    for path in REQUIRED:
        value = get(doc, path)
        if value in (None, "", [], {}):
            errors.append(f"missing: {path}")

    for path in LOCATOR_PATHS:
        check_locator(doc, path, errors)

    trays = check_trays(doc, errors, warnings)
    boards = check_boards(doc, errors, warnings)

    mode = get(doc, "business.mode")
    if mode and mode not in MODES:
        errors.append(f"business.mode must be one of {sorted(MODES)}, got {mode!r}")

    points = get(doc, "business.pointsMode") or "none"
    if points not in POINTS_MODES:
        errors.append(f"business.pointsMode must be one of {sorted(POINTS_MODES)}, got {points!r}")
    if points != "none" and get(doc, "fields.optional.points") is None:
        errors.append("business.pointsMode is points but fields.optional.points has no locator")

    if get(doc, "business.caseSku", True):
        label = get(doc, "fields.spec.caseSkuLabel")
        if not label:
            errors.append("business.caseSku is on but fields.spec.caseSkuLabel is missing")
        elif "{" not in label:
            warnings.append(f"caseSkuLabel {label!r} has no {{per}}/{{unit}}/{{cu}} placeholder; every case row will read the same")
    else:
        warnings.append("business.caseSku is off: queue emits single-unit rows only")

    order = get(doc, "fields.spec.skuFieldOrder")
    if not isinstance(order, list) or not order:
        errors.append("fields.spec.skuFieldOrder must list the numeric inputs of a SKU row, in page order")
    elif "price" not in order:
        errors.append("fields.spec.skuFieldOrder must contain 'price'")

    stock = get(doc, "defaults.stock")
    if stock in (None, ""):
        warnings.append("defaults.stock not set - every queue item must carry its own stock")
    elif not isinstance(stock, (int, float)) or stock <= 0:
        errors.append(f"defaults.stock must be a positive number, got {stock!r}")

    min_bytes = get(doc, "image.minBytes")
    if not min_bytes:
        warnings.append("image.minBytes missing, default 50000 will be used")
    elif not isinstance(min_bytes, int) or min_bytes < 1024:
        errors.append(f"image.minBytes must be an integer of bytes, got {min_bytes!r}")

    if not get(doc, "fields.submit.successWhen"):
        warnings.append("fields.submit.successWhen missing, default 'route-changed' will be used")
    if not get(doc, "probedAt"):
        warnings.append("probedAt missing - profile provenance unknown")
    if get(doc, "fields.scope") is None:
        warnings.append("no fields.scope: the form has no region/scope picker, or the probe missed it")

    if not args.quiet:
        print("profile summary")
        print(f"  platform      : {get(doc, 'platform')}")
        print(f"  new form      : {get(doc, 'form.newUrl')}")
        print(f"  product list  : {get(doc, 'form.listUrl')}")
        print(f"  root category : {get(doc, 'defaults.rootCategory')}")
        print(f"  spec name     : {get(doc, 'defaults.specName') or '-'}")
        print(f"  stock         : {stock if stock not in (None, '') else '-'}")
        print(f"  scope         : {get(doc, 'fields.scope.value') or '-'}")
        print(f"  sku row order : {order}")
        submit = get(doc, "fields.submit.button")
        print(f"  submit        : {submit.get('value') if isinstance(submit, dict) else '-'}")
        print(f"  mode          : {mode}")
        print(f"  points        : {points}   minCoupon: {get(doc, 'business.minCoupon') or 0}   counters: {get(doc, 'business.counters')}")
        if trays:
            print("  upload trays  :")
            for tray in trays:
                if not isinstance(tray, dict):
                    continue
                print(f"    - {tray.get('key')}: {tray.get('label') or '-'}"
                      f"  bind={tray.get('bind', 'product')} crop={tray.get('crop', 'square')}"
                      f" max={tray.get('maxCount', 1)}{' (optional)' if tray.get('optional') else ''}")
        if boards:
            top_fields = (doc.get("fields") or {}).get("spec", {}).get("skuFieldOrder") or []
            real = {k: b for k, b in boards.items() if isinstance(b, dict)}
            print(f"  boards        : {len(real)} section(s) - --board selects one")
            for key, board in sorted(real.items()):
                if not isinstance(board, dict):
                    continue
                b_form = board.get("form") or {}
                b_def = board.get("defaults") or {}
                b_fields = board.get("fields") or {}
                b_biz = board.get("business") or {}
                print(f"    [{key}] {board.get('label')}")
                print(f"      new form   : {b_form.get('newUrl') or get(doc, 'form.newUrl')}")
                print(f"      list       : {b_form.get('listUrl') or get(doc, 'form.listUrl')}")
                print(f"      root cat   : {b_def.get('rootCategory') or get(doc, 'defaults.rootCategory')}")
                scope_value = ((b_fields.get("scope") or {}).get("value")
                               or get(doc, "fields.scope.value") or "-")
                print(f"      scope      : "
                      f"{'absent on this section' if 'scope' in (b_fields.get('without') or []) else scope_value}")
                order = b_fields.get("spec", {}).get("skuFieldOrder") or top_fields
                print(f"      sku order  : {order}")
                print(f"      pre-checks : {b_def.get('checkedToStart', get(doc, 'defaults.checkedToStart'))}")
                if "couponOffset" in b_biz:
                    print(f"      coupon     : {b_biz['couponOffset']:+g} versus the base coupon")
        notes = get(doc, "notes") or []
        if notes:
            print("  notes:")
            for n in notes:
                print("    -", n)

    for w in warnings:
        print("WARN", w)
    for e in errors:
        print("ERR ", e)
    print(f"\n{'profile OK' if not errors else 'profile INCOMPLETE: re-run the probe for the missing parts'}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
