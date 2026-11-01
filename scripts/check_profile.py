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

REQUIRED = [
    "platform",
    "form.newUrl",
    "image.uploadTrigger",
    "fields.name.locator",
    "fields.category.kind",
    "fields.category.locator",
    "fields.spec.nameLocator",
    "fields.spec.valueLocator",
    "fields.spec.skuInputsLocator",
    "fields.submit.button",
    "defaults.rootCategory",
    "business.mode",
]
LOCATOR_PATHS = [
    "image.uploadTrigger",
    "image.cropConfirm",
    "image.thumbLocator",
    "fields.name.locator",
    "fields.category.locator",
    "fields.spec.nameLocator",
    "fields.spec.valueLocator",
    "fields.spec.skuInputsLocator",
    "fields.submit.button",
    "fields.submit.confirmButton",
]
MODES = {"fill-only", "fill-and-submit"}
POINTS_MODES = {"none", "coupon", "fixed"}


def get(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


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
        loc = get(doc, path)
        if loc is None:
            continue
        if not isinstance(loc, dict) or not loc.get("by") or not loc.get("value"):
            errors.append(f"bad locator: {path} (want {{'by': ..., 'value': ...}})")

    mode = get(doc, "business.mode")
    if mode and mode not in MODES:
        errors.append(f"business.mode must be one of {sorted(MODES)}, got {mode!r}")

    points = get(doc, "business.pointsMode") or "none"
    if points not in POINTS_MODES:
        errors.append(f"business.pointsMode must be one of {sorted(POINTS_MODES)}, got {points!r}")

    if not get(doc, "image.minBytes"):
        warnings.append("image.minBytes missing, default 50000 will be used")
    if not get(doc, "fields.submit.successWhen"):
        warnings.append("fields.submit.successWhen missing, default 'route-changed' will be used")
    if not get(doc, "probedAt"):
        warnings.append("probedAt missing - profile provenance unknown")

    if not args.quiet:
        print("profile summary")
        print(f"  platform      : {get(doc, 'platform')}")
        print(f"  new form      : {get(doc, 'form.newUrl')}")
        print(f"  root category : {get(doc, 'defaults.rootCategory')}")
        print(f"  spec name     : {get(doc, 'defaults.specName') or '-'}")
        print(f"  scope         : {get(doc, 'fields.scope.value') or '-'}")
        print(f"  submit        : {get(doc, 'fields.submit.button.value') if isinstance(get(doc, 'fields.submit.button'), dict) else '-'}")
        print(f"  mode          : {mode}")
        print(f"  points        : {points}   minCoupon: {get(doc, 'business.minCoupon') or 0}   counters: {get(doc, 'business.counters')}")
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
