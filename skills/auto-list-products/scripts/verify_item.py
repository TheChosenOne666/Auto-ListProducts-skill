#!/usr/bin/env python3
"""Verify one filled listing against its queue item, before it is allowed to be submitted.

The fill loop ends with "verify every field" - and if that verification is written fresh each time,
its strictness drifts between the first product and the hundred-and-twentieth. This script is that
verification made fixed: it reads the platform profile, the queue item and a read-back dump of the
form, and prints one PASS / FAIL / SKIP line per rule plus a verdict. Submitting after a FAIL is the
expensive mistake: a reviewed listing cannot be edited, it has to be deleted and relisted.

Usage
  python verify_item.py --profile profile.json --queue queue.json --n 7 --actual actual.json
  python verify_item.py --profile profile.json --item item.json  --actual actual.json
  options: --out verdict.json  --tolerance 0.005  --quiet

actual.json is what the agent reads back off the form (see actual.example.json). Nothing here opens
a browser or touches the shop: it compares three local files and is deterministic about it.

Exit codes: 0 every rule passed, 1 at least one failed (do not submit), 2 unusable input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_MIN_BYTES = 50000
# forms render a category path with different glyphs; comparing segments keeps the exact-match rule
# without forcing the operator to retype the breadcrumb in the profile's separator
PATH_SEP = re.compile(r"\s*(?:/|>|›|»|→)\s*")


class InputError(Exception):
    """Raised when a comparison cannot be made at all, as opposed to failing."""


def load_json(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"no such file: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON: {exc}")
    except UnicodeDecodeError as exc:
        raise InputError(f"{path} is not UTF-8 text ({exc}); dump the read-back as UTF-8")
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc}")


def path_segments(value) -> list[str]:
    return [s.strip() for s in PATH_SEP.split(str(value or "").strip()) if s.strip()]


def num(value):
    """A real number, or None: booleans and strings never count as a numeric field."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def align_rows(rows: list, order: list) -> list[dict]:
    """Accept either [{"price":..}, ..] or [[3.0, 10000, 0.8], ..] keyed by skuFieldOrder."""
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, list):
            if not order:
                raise InputError("skuRows given as arrays but the profile has no fields.spec.skuFieldOrder")
            out.append(dict(zip(order, row)))
        else:
            raise InputError(f"skuRows entry {row!r} is neither an object nor an array")
    return out


def check(results: list, name: str, verdict: str, detail: str = ""):
    results.append({"check": name, "verdict": verdict, "detail": detail})


def verify_name(results, item, actual, profile):
    want = str(item.get("name", "")).strip()
    max_len = profile.get("fields", {}).get("name", {}).get("maxLength")
    if isinstance(max_len, int) and len(want) > max_len:
        # the plan itself cannot fit the form: caught before anyone types it
        check(results, "name", "FAIL", f"the queue name is {len(want)} characters; the form takes at most {max_len}")
        return
    if "name" not in actual:
        check(results, "name", "FAIL", "read-back has no name")
        return
    got = str(actual.get("name", "")).strip()
    if got != want:
        check(results, "name", "FAIL", f"expected {want!r} got {got!r}")
    else:
        check(results, "name", "PASS", got)


def verify_category(results, item, actual):
    want = item.get("expectCat")
    if not want:
        check(results, "category", "SKIP", "the queue item carries no expectCat")
        return
    got = actual.get("category")
    if got is None:
        check(results, "category", "FAIL", "read-back has no category")
        return
    want_parts, got_parts = path_segments(want), path_segments(got)
    if got_parts != want_parts:
        # segment by segment, exact: a parent node whose label contains the leaf is not a match,
        # and one missing level is never rounded up to close enough
        check(results, "category", "FAIL", f"expected {want_parts} got {got_parts}")
    else:
        check(results, "category", "PASS", " / ".join(got_parts))


def verify_scope(results, item, actual, profile):
    scope = profile.get("fields", {}).get("scope")
    if not scope or not scope.get("value"):
        check(results, "scope", "SKIP", "profile declares no scope field on this form")
        return
    want, got = str(scope["value"]), actual.get("scope")
    if got is None:
        check(results, "scope", "FAIL", f"read-back has no scope; the form should show {want!r}")
    elif str(got).strip() != want:
        check(results, "scope", "FAIL", f"expected {want!r} got {str(got).strip()!r}")
    else:
        check(results, "scope", "PASS", str(got).strip())


def verify_spec_name(results, item, actual, profile):
    want = item.get("specName") or profile.get("defaults", {}).get("specName")
    if not want:
        check(results, "specName", "SKIP", "neither the queue item nor the profile names the spec")
        return
    got = actual.get("specName")
    if got is None:
        check(results, "specName", "FAIL", f"read-back has no spec name; expected {want!r}")
    elif str(got).strip() != str(want).strip():
        check(results, "specName", "FAIL", f"expected {want!r} got {str(got).strip()!r}")
    else:
        check(results, "specName", "PASS", str(got).strip())


def read_values(results, actual):
    values = actual.get("specValues")
    if not isinstance(values, list):
        check(results, "spec values", "FAIL", "read-back has no specValues list")
        return None
    return values


def verify_values(results, item, values, allow_extra):
    """The form must hold exactly the spec values the plan declares - no more than it declares."""
    if values is None:
        return
    declared = item.get("specValues")
    wanted = list(declared) if isinstance(declared, list) and declared else \
        [v for v in (item.get("v1"), item.get("v2")) if v]
    if not wanted:
        check(results, "spec values", "SKIP", "the queue item declares no spec values")
        return
    absent = [v for v in wanted if v not in values]
    extra = [v for v in values if v not in wanted]
    if absent:
        check(results, "spec values", "FAIL", f"{absent} not on the form; found {values}")
    elif extra and not allow_extra:
        check(results, "spec values", "FAIL",
              f"the form holds {extra}, which this queue item does not declare - its numbers would go "
              "uncompared. List every variant in the item's specValues, or pass --allow-extra-specs")
    elif extra:
        check(results, "spec values", "PASS", f"{values} ({len(extra)} beyond the plan, their numbers are not compared)")
    else:
        check(results, "spec values", "PASS", str(values))

    v1, v2 = item.get("v1"), item.get("v2")
    if v1 and v2 and v1 in values and v2 in values and values.index(v1) > values.index(v2):
        check(results, "spec order", "FAIL",
              f"the case row {v2!r} sits before the unit row {v1!r}; rows can only be appended")
    else:
        check(results, "spec order", "PASS", f"{v1} before {v2 or '(no case row)'}")


def plan_rows(item, values):
    """Which SKU row index the queue item has numbers for, and what they should be."""
    plan = {}
    for value, price, points in ((item.get("v1"), item.get("price1"), item.get("pt1")),
                                 (item.get("v2"), item.get("price2"), item.get("pt2"))):
        if value and value in values:
            plan[values.index(value)] = (price, points)
    return plan


def verify_rows(results, item, actual, values, profile, tolerance):
    order = profile.get("fields", {}).get("spec", {}).get("skuFieldOrder") or []
    rows = actual.get("skuRows")
    if not isinstance(rows, list):
        check(results, "sku rows", "FAIL", "read-back has no skuRows list")
        return
    if values is not None and len(rows) != len(values):
        check(results, "sku rows", "FAIL", f"{len(rows)} rows for {len(values)} spec values")
        return
    if not rows:
        check(results, "sku rows", "FAIL", "no SKU rows read back")
        return
    aligned = align_rows(rows, order)
    plan = plan_rows(item, values or [])
    check(results, "sku rows", "PASS", f"{len(aligned)} rows, fields {order or 'unlabelled'}")

    for field, index in (("price", 0), ("points", 1)):
        expected = [(idx, want) for idx, (price, points) in sorted(plan.items())
                    for want in [(price, points)[index]] if want is not None]
        if not expected:
            check(results, f"sku {field}", "SKIP", f"the queue item declares no {field}")
            continue
        bad = []
        for idx, want in expected:
            got = num(aligned[idx].get(field)) if idx < len(aligned) else None
            if got is None:
                bad.append(f"row {idx + 1}: nothing read back for {field}")
            elif abs(got - float(want)) > tolerance:
                bad.append(f"row {idx + 1}: expected {want} got {got}")
        if bad:
            check(results, f"sku {field}", "FAIL", "; ".join(bad))
        else:
            check(results, f"sku {field}", "PASS", ", ".join(f"row {i + 1}={w}" for i, w in expected))

    unchecked = [idx + 1 for idx in range(len(aligned)) if idx not in plan]
    if unchecked:
        check(results, "sku rows compared", "SKIP",
              f"rows {unchecked} have no values in the queue item; declare them in specValues to check them")

    want_stock = num(item.get("stock"))
    if want_stock is None:
        check(results, "sku stock", "SKIP", "the queue item carries no stock")
        return
    bad = []
    for idx in range(len(aligned)):
        got = num(aligned[idx].get("stock"))
        if got is None:
            bad.append(f"row {idx + 1}: no stock read back")
        elif got != want_stock:
            bad.append(f"row {idx + 1}: expected {want_stock} got {got}")
    if bad:
        check(results, "sku stock", "FAIL", "; ".join(bad))
    else:
        check(results, "sku stock", "PASS", f"{want_stock} on every row")


def verify_counters(results, item, actual):
    for field in ("sales", "fav"):
        want = num(item.get(field))
        if want is None:
            check(results, field, "SKIP", "the queue item has no display counter")
            continue
        got = num(actual.get(field))
        if got is None:
            check(results, field, "FAIL", f"queue says {want}, the form shows nothing")
        elif got != want:
            check(results, field, "FAIL", f"expected {want} got {got}")
        else:
            check(results, field, "PASS", str(got))


def verify_images(results, actual, profile):
    cfg = profile.get("image", {})
    floor = cfg.get("minBytes")
    # an explicit 0 means "no floor on this platform"; a missing or nonsense value falls back
    if not isinstance(floor, int) or isinstance(floor, bool) or floor < 0:
        floor = DEFAULT_MIN_BYTES
    trays = [t for t in (cfg.get("trays") or []) if isinstance(t, dict)]
    required = [t for t in trays if t.get("bind", "product") == "product" and not t.get("optional")]
    if not required:
        check(results, "images", "SKIP", "profile declares no required product tray")
        return
    shots = actual.get("images")
    by_tray = {}
    if isinstance(shots, list):
        for shot in shots:
            if isinstance(shot, dict) and shot.get("tray"):
                by_tray[str(shot["tray"])] = shot
    bad = []
    for tray in required:
        key = str(tray.get("key"))
        shot = by_tray.get(key)
        if shot is None:
            bad.append(f"{key}: nothing read back for this upload slot")
            continue
        stored = num(shot.get("bytes"))
        if stored is None:
            bad.append(f"{key}: no stored byte size (fetch the thumbnail URL, do not trust the file name)")
        elif stored < floor:
            bad.append(f"{key}: stored {stored} bytes, under the {floor} floor - that is a white crop")
    if bad:
        check(results, "images", "FAIL", "; ".join(bad))
    else:
        check(results, "images", "PASS",
              ", ".join(f"{t.get('key')}={by_tray[str(t.get('key'))].get('bytes')}B" for t in required))


def verify_checks(results, actual, profile):
    declared = profile.get("defaults", {}).get("checkedToStart")
    if not isinstance(declared, list) or not declared:
        check(results, "pre-checked", "SKIP", "profile declares no pre-checked controls")
        return
    got = num(actual.get("checked"))
    if got is None:
        check(results, "pre-checked", "FAIL", f"expected {len(declared)} controls checked, read-back has none")
    elif got != len(declared):
        check(results, "pre-checked", "FAIL", f"{int(got)} checked, the fresh form was probed with {len(declared)}")
    else:
        check(results, "pre-checked", "PASS", f"{len(declared)} as probed")


def verify_mode(results, actual, profile):
    mode = profile.get("business", {}).get("mode", "fill-only")
    submitted = bool(actual.get("submitted"))
    if mode == "fill-only" and submitted:
        check(results, "submit mode", "FAIL",
              "OUT-OF-MODE: the agreed mode is fill-only, but the form was submitted")
    else:
        check(results, "submit mode", "PASS", f"{mode}, submitted={submitted}")


def verify(profile: dict, item: dict, actual: dict, tolerance: float, allow_extra: bool = False) -> list[dict]:
    results: list[dict] = []
    verify_name(results, item, actual, profile)
    verify_category(results, item, actual)
    verify_scope(results, item, actual, profile)
    verify_spec_name(results, item, actual, profile)
    values = read_values(results, actual)
    verify_values(results, item, values, allow_extra)
    verify_rows(results, item, actual, values, profile, tolerance)
    verify_counters(results, item, actual)
    verify_images(results, actual, profile)
    verify_checks(results, actual, profile)
    verify_mode(results, actual, profile)
    return results


def print_results(results: list[dict], item, quiet: bool) -> int:
    failed = [r for r in results if r["verdict"] == "FAIL"]
    skipped = [r for r in results if r["verdict"] == "SKIP"]
    print(f"verify item #{item.get('n', '?')} {item.get('name', '')}")
    for row in results:
        if quiet and row["verdict"] == "PASS":
            continue
        print(f"{row['verdict']} {row['check']:<14} {row['detail']}")
    if failed:
        print(f"\nVERDICT: blocked - {len(failed)} failed, {len(skipped)} skipped. Do not submit this item.")
        return 1
    print(f"\nVERDICT: ok - {len([r for r in results if r['verdict'] == 'PASS'])} passed, {len(skipped)} skipped")
    return 0


def pick_item(args) -> dict:
    if args.item:
        raw = load_json(args.item)
        if not isinstance(raw, dict):
            raise InputError(f"{args.item}: expected one queue item object")
        return raw
    if not args.queue or args.n is None:
        raise InputError("pass --item item.json, or --queue queue.json together with --n")
    raw = load_json(args.queue)
    if isinstance(raw, dict):
        raw = raw.get("items")
    if not isinstance(raw, list):
        raise InputError(f"{args.queue}: expected a list of queue items")
    for entry in raw:
        if isinstance(entry, dict) and entry.get("n") == args.n:
            return entry
    raise InputError(f"{args.queue}: no item with n={args.n}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, help="profile.json from the probe phase")
    ap.add_argument("--actual", required=True, help="read-back of the filled form (actual.example.json)")
    ap.add_argument("--queue", help="queue.json to pick the item from")
    ap.add_argument("--n", type=int, help="the queue item number, with --queue")
    ap.add_argument("--item", help="a single queue item already extracted to a file")
    ap.add_argument("--tolerance", type=float, default=0.005, help="numeric tolerance (default 0.005)")
    ap.add_argument("--allow-extra-specs", action="store_true",
                    help="the product really has more variants than v1/v2 (colours, sizes); rows beyond the "
                         "plan are then reported as unchecked instead of blocking the submit")
    ap.add_argument("--out", help="write the machine-readable verdict here")
    ap.add_argument("--quiet", action="store_true", help="print only failures, skips and the verdict")
    args = ap.parse_args()

    try:
        profile = load_json(args.profile)
        if not isinstance(profile, dict) or "fields" not in profile:
            raise InputError(f"{args.profile}: not a profile (no fields section); run check_profile.py first")
        item = pick_item(args)
        actual = load_json(args.actual)
        if not isinstance(actual, dict):
            raise InputError(f"{args.actual}: expected an object of read-back values")
    except InputError as exc:
        print(f"cannot verify: {exc}", file=sys.stderr)
        return 2

    try:
        results = verify(profile, item, actual, args.tolerance, args.allow_extra_specs)
    except InputError as exc:
        print(f"cannot verify: {exc}", file=sys.stderr)
        return 2

    code = print_results(results, item, args.quiet)
    if args.out:
        try:
            payload = {"n": item.get("n"), "name": item.get("name"), "verdict": "ok" if code == 0 else "blocked",
                        "checks": results}
            Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"verdict written: {args.out}")
        except OSError as exc:
            # the verdict stands even if the archive could not be written; losing it silently is worse
            print(f"WARN cannot write the verdict to {args.out}: {exc}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
