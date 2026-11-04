#!/usr/bin/env python3
"""Reconcile what the shop actually holds against what the queue or the sheet says it should hold.

Inputs are files, never the live backend: the agent scrapes the product list into a snapshot and this
script only reads it. The audit stays repeatable, offline and free of any side effect on the shop.

Expected sources (format auto-detected by extension):
  queue.json   make_queue.py output - name, price1, v1, price2, v2, pt1, pt2, optional board
  sheet.xlsx   the registration workbook, read with --sheet and --cols (name, retail, coupon).
               A sheet carries no spec names, so the structure dimension is reported as skipped
               rather than silently passing.

Snapshot shape (see listings.example.json):
  {"board": "pickup", "total": 686, "items": [
     {"id": "SPU202610241058190914", "name": "...", "status": "on-sale",
      "specs": [{"name": "500ml*1瓶", "price": 3.0, "points": 0.8}]}]}
  A bare list of items is accepted too. `total` is checked against len(items), because a page missed
  while paging turns into fake "missing" rows - and a fake missing row is how a duplicate listing gets
  created.

Matching is name + price + spec structure together, never name alone: the same name at a different
price is a real second product, and a name-only match is how an old listing silently hides a wrong one.

Two folding rules matter and are easy to get wrong:
  * product names drop case-pack notation (`*24罐`, `（箱*24瓶）`) but keep a trailing size, so
    洗发水*200ml and 洗发水*400ml stay two products;
  * spec labels are folded weakly (spacing and punctuation only) and keep their count, because
    `500ml*1瓶` vs `500ml*2瓶` is exactly the difference the structure check exists to catch.

Prices, points and coupons are compared only when both sides carry the field; a coupon that never
reached the shop surfaces in the PRICE list with both numbers.

Usage
  python reconcile.py --expected queue.json --listings listings.json
  python reconcile.py --expected sheet.xlsx --sheet 登记 --listings l-pickup.json \
      --listings l-express.json --board pickup
  options: --expected-format auto|queue|sheet  --out diff.json  --show-extras
           --price-tolerance 0.005  --verbose

Exit codes: 0 no differences, 1 differences found, 2 unusable input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# a snapshot's status column is whatever the page shows, in either language
STATUS_WORDS = {
    "on-sale": "on-sale", "selling": "on-sale", "approved": "on-sale", "active": "on-sale",
    "已上架": "on-sale", "销售中": "on-sale", "上架中": "on-sale", "在售": "on-sale",
    "pending-review": "pending-review", "待审核": "pending-review", "审核中": "pending-review",
    "pending-submit": "pending-submit", "待提交": "pending-submit", "draft": "pending-submit",
    "草稿": "pending-submit",
    "rejected": "rejected", "已驳回": "rejected", "驳回": "rejected",
    "recycled": "recycled", "deleted": "recycled", "回收站": "recycled", "已删除": "recycled",
}
PACK_WORDS = "箱提件袋盒组份"
CONTAINERS = "瓶罐盒袋听支件包个只箱提卷条双顶桶份斤"
WIDTH_MAP = {ord("（"): "(", ord("）"): ")", ord("【"): "[", ord("】"): "]",
             ord("　"): "", ord("，"): ",", ord("×"): "*", ord("Ｘ"): "x", ord("ｘ"): "x"}


class InputError(Exception):
    """Raised for anything that makes a comparison meaningless rather than merely different."""


def fold(value) -> str:
    return re.sub(r"\s+", "", str(value).translate(WIDTH_MAP).lower())


def normalize_name(value: str) -> str:
    """Fold a product title to the identity the audit compares on.

    Punctuation and case are folded and case-pack notation is removed - `（箱*24瓶）`, `(1*24罐)`,
    `*24罐` - because the same product is written all four ways across a sheet, a queue and a list page.
    Only container words are treated as pack counts: `*400ml` is a size and stays in the name, or
    洗发水*200ml and 洗发水*400ml would reconcile as one product.
    """
    text = fold(value)
    for _ in range(3):
        stripped = re.sub(r"\(([^()]*)\)\s*$", lambda m: m.group(1) if keep_paren(m.group(1)) else "", text)
        if stripped == text:
            break
        text = stripped
    while True:
        stripped = re.sub(rf"[*/]\d+[{CONTAINERS}](?:装)?$", "", text)
        if stripped == text:
            break
        text = stripped
    return text.strip("-*/_ .,")


def normalize_spec(value) -> str:
    """Fold a spec-row label for the structure dimension, keeping its count.

    Deliberately weaker than the product-name fold: `500ml*1瓶` and `500ml*2瓶` must stay different,
    or the structure check cannot see a shop that shipped the wrong spec rows.
    """
    return fold(value if value is not None else "")


def keep_paren(inner: str) -> bool:
    """True when a trailing bracket group is part of the name, false when it is pack notation."""
    if not any(ch.isdigit() for ch in inner):
        return True
    if any(word in inner for word in PACK_WORDS):
        return False
    return not re.fullmatch(r"\d+[*x]\d+[^\s]*", inner)


def entry_price(specs: list) -> float | None:
    """The price a listing sits at on the shelf: the cheapest real unit price.

    A zero-price row (a bundled gift line some platforms add) is skipped, because taking 0 as the
    price would report every real product as a price mismatch.
    """
    prices = [s.get("price") for s in specs if isinstance(s.get("price"), (int, float))]
    positive = [p for p in prices if p > 0]
    if positive:
        return min(positive)
    return min(prices) if prices else None


def created_from_id(item_id) -> str:
    """The platform's SPU code starts with its creation timestamp: SPU202610241058190914."""
    match = re.match(r"[^\d]*(\d{14})", str(item_id or ""))
    if not match:
        return "-"
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "-"


def load_json(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"no such file: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON: {exc}")


def load_expected(path: str, fmt_hint: str, sheet: str, cols: str, board: str | None) -> list[dict]:
    suffix = Path(path).suffix.lower()
    if fmt_hint == "auto":
        fmt_hint = "queue" if suffix == ".json" else "sheet"
    if fmt_hint == "queue":
        return expected_from_queue(path, board)
    if suffix not in (".xlsx", ".xlsm"):
        raise InputError(f"{path}: not a workbook ({suffix or 'no extension'}); "
                         "pass a queue .json or an .xlsx/.xlsm sheet")
    return expected_from_sheet(path, sheet, cols, board)


def expected_from_queue(path: str, board: str | None) -> list[dict]:
    raw = load_json(path)
    if isinstance(raw, dict):
        raw = raw.get("items")
    if not isinstance(raw, list):
        raise InputError(f"{path}: expected a list of queue items")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not item.get("name") or item.get("price1") is None:
            raise InputError(f"{path}[{i}]: needs at least name and price1")
        specs = [item["v1"]] if item.get("v1") else []
        if item.get("v2"):
            specs.append(item["v2"])
        out.append({"key": item.get("n", i), "name": str(item["name"]), "price": float(item["price1"]),
                    "points": item.get("pt1"), "coupon": item.get("coupon"), "specs": specs,
                    "board": item.get("board") or board})
    return out


def expected_from_sheet(path: str, sheet: str, cols: str, board: str | None) -> list[dict]:
    try:
        import openpyxl
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError:
        raise InputError("reading a workbook needs openpyxl: pip install openpyxl")
    name_col, retail_col, coupon_col, _, _ = [c.strip() for c in cols.split(",")]
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except FileNotFoundError:
        raise InputError(f"no such file: {path}")
    except InvalidFileException as exc:
        raise InputError(f"{path}: not a readable workbook ({exc})")
    if sheet not in wb.sheetnames:
        raise InputError(f"{path}: sheet {sheet!r} not in {wb.sheetnames}")
    ws = wb[sheet]
    out = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        cells = {c.column_letter: c.value for c in row}
        name = cells.get(name_col)
        if name in (None, ""):
            continue
        retail = cells.get(retail_col)
        if not isinstance(retail, (int, float)):
            raise InputError(f"{path}: {ws.title} row {row[0].row} has no numeric retail price in {retail_col}")
        coupon = cells.get(coupon_col)
        out.append({"key": row[0].row, "name": str(name), "price": float(retail),
                    "points": None, "coupon": coupon if isinstance(coupon, (int, float)) else None,
                    "specs": [], "board": board})
    if not out:
        raise InputError(f"{path}: no rows found in {sheet!r} below row 1")
    return out


def load_listings(paths: list[str], board: str | None) -> list[dict]:
    entries = []
    for path in paths:
        raw = load_json(path)
        file_board = board
        captured = "-"
        if isinstance(raw, dict):
            file_board = raw.get("board") or board
            captured = str(raw.get("capturedAt") or "-")
            total = raw.get("total")
            items = raw.get("items")
            if not isinstance(items, list):
                raise InputError(f"{path}: wrapper object has no items list")
            if isinstance(total, int) and total != len(items):
                raise InputError(f"{path}: snapshot claims total={total} but holds {len(items)} rows - "
                                 "a page is missing; scrape again before trusting any missing report")
        else:
            items = raw
        if not isinstance(items, list):
            raise InputError(f"{path}: expected a list, or an object with items")
        for i, item in enumerate(items):
            if not isinstance(item, dict) or not item.get("name"):
                raise InputError(f"{path}[{i}]: every snapshot row needs a name")
            specs = item.get("specs")
            if specs is None and item.get("price") is not None:
                specs = [{"name": None, "price": item["price"]}]
            if not isinstance(specs, list):
                raise InputError(f"{path}[{i}]: specs must be a list (or give the row a price)")
            raw_status = str(item.get("status", "")).strip()
            entries.append({"id": item.get("id"), "name": str(item["name"]),
                            "norm": normalize_name(item["name"]),
                            "specs": specs,
                            "spec_names": [normalize_spec(s.get("name")) for s in specs if s.get("name")],
                            "price": entry_price(specs),
                            # an unrecognised status is unknown, never guessed into a known bucket
                            "status": STATUS_WORDS.get(raw_status.lower(), "unknown"),
                            "status_raw": raw_status or "unknown",
                            "board": item.get("board") or file_board,
                            "captured": item.get("capturedAt") or captured,
                            "created": item.get("createdAt") or created_from_id(item.get("id"))})
    return entries


def fmt(value) -> str:
    return "-" if value is None else str(value)


def money(value) -> str:
    """Prices print with two decimals: 3.0 must not render as 3, which reads like a different number."""
    return "-" if value is None else f"{float(value):.2f}"


def spec_row(entry: dict, want: dict):
    """The snapshot's spec row that should carry the plan's single-unit price, and that price.

    Matching the row by spec label first means a zero-price gift line or a cheap case row cannot
    decide the verdict on its own.
    """
    if want["specs"]:
        wanted = normalize_spec(want["specs"][0])
        for row in entry["specs"]:
            if normalize_spec(row.get("name")) == wanted and isinstance(row.get("price"), (int, float)):
                return row, float(row["price"])
    return None, entry["price"]


def reconcile(expected: list[dict], listings: list[dict], tolerance: float = 0.005) -> dict:
    index: dict[tuple, list[dict]] = {}
    for entry in listings:
        index.setdefault((entry["board"], entry["norm"]), []).append(entry)

    report = {"missing": [], "duplicates": [], "price": [], "structure": [], "status": [], "unknown_status": []}
    consumed: set[tuple] = set()

    for want in expected:
        key = (want["board"], want["norm"] if "norm" in want else normalize_name(want["name"]))
        consumed.add((want["board"], key[1]))
        hits = index.get(key, [])
        if not hits:
            report["missing"].append({"name": want["name"], "key": want["key"], "board": want["board"],
                                      "price": want["price"], "specs": want["specs"]})
            continue

        scored = [(abs(price - want["price"]), entry, row, price)
                  for entry in hits for row, price in [spec_row(entry, want)] if price is not None]
        priced = [s for s in scored if s[0] <= tolerance]
        chosen = priced[0] if priced else (min(scored, key=lambda s: s[0]) if scored else (None, hits[0], None, None))
        _delta, entry, row, actual = chosen

        def note(kind, **fields):
            row_out = {"name": want["name"], "key": want["key"], "board": want["board"], **fields}
            report[kind].append(row_out)

        if not priced:
            note("price", expected=want["price"], actual=actual, id=entry["id"], created=entry["created"])
        else:
            for field, label in (("points", want.get("points")), ("coupon", want.get("coupon"))):
                if label is None:
                    continue
                source = row if row is not None else {}
                found = source.get(field)
                if found is None:
                    found = next((s.get(field) for s in entry["specs"] if s.get(field) is not None), None)
                if isinstance(found, (int, float)) and abs(float(found) - float(label)) > tolerance:
                    note("price", field=field, expected=label, actual=found, id=entry["id"], created=entry["created"])

            if want["specs"]:
                absent = [s for s in want["specs"] if normalize_spec(s) not in entry["spec_names"]]
                if absent:
                    note("structure", expected_specs=want["specs"], missing_specs=absent,
                         actual_specs=[s.get("name") for s in entry["specs"]],
                         id=entry["id"], created=entry["created"])

        if entry["status"] != "on-sale":
            target = "unknown_status" if entry["status"] == "unknown" else "status"
            note(target, status=entry["status"], status_raw=entry["status_raw"],
                 id=entry["id"], created=entry["created"])

    cents: dict[tuple, list[dict]] = {}
    for entry in listings:
        price = None if entry["price"] is None else round(float(entry["price"]), 2)
        cents.setdefault((entry["board"], entry["norm"], price), []).append(entry)
    for (_board, _norm, price), rows in sorted(cents.items(), key=lambda kv: str(kv[0])):
        if len(rows) > 1:
            report["duplicates"].append({"name": rows[0]["name"], "board": _board, "price": price,
                                         "count": len(rows), "ids": [r["id"] for r in rows],
                                         "created": [r["created"] for r in rows]})

    extras = [e for e in listings if (e["board"], e["norm"]) not in consumed]
    report["extras"] = {"count": len(extras),
                        "items": [{"name": e["name"], "board": e["board"], "price": e["price"],
                                   "id": e["id"], "status": e["status"], "created": e["created"]}
                                  for e in extras]}
    return report


def print_report(report: dict, expected: list[dict], listings: list[dict], specs_known: bool,
                 show_extras: bool, verbose: bool) -> int:
    boards = sorted({fmt(e["board"]) for e in expected} | {fmt(e["board"]) for e in listings})
    captured = sorted({str(e.get("captured", "-")) for e in listings})
    print(f"reconcile  expected {len(expected)}   snapshot rows {len(listings)}   "
          f"boards: {', '.join(boards)}   captured: {', '.join(captured)}")

    total = 0
    for kind, title in (("missing", "MISSING - in the plan, not in the shop"),
                        ("duplicates", "DUPLICATE - same name and price listed more than once"),
                        ("price", "PRICE - name matches but the number does not"),
                        ("structure", "STRUCTURE - price matches but the spec rows do not"),
                        ("status", "STATUS - found, but not live on the shelf")):
        rows = report[kind]
        total += len(rows)
        print(f"\n{title}: {len(rows)}")
        for row in rows:
            if kind == "missing":
                print(f"  - {row['name']}  board={fmt(row['board'])}  want {money(row['price'])} "
                      f"specs={row['specs'] or '-'}")
            elif kind == "duplicates":
                print(f"  - {row['name']}  board={fmt(row['board'])}  price={money(row['price'])} x{row['count']} "
                      f"ids={row['ids']}")
            elif kind == "price":
                field = row.get("field", "price")
                print(f"  - {row['name']}  board={fmt(row['board'])}  {field} 期望 {money(row['expected'])} "
                      f"实际 {money(row['actual'])}  id={fmt(row['id'])} 建于 {row['created']}")
            elif kind == "structure":
                print(f"  - {row['name']}  board={fmt(row['board'])}  缺规格 {row['missing_specs']} "
                      f"实际 {row['actual_specs']}  id={fmt(row['id'])} 建于 {row['created']}")
            else:
                print(f"  - {row['name']}  board={fmt(row['board'])}  {row['status_raw']} "
                      f"id={fmt(row['id'])} 建于 {row['created']}")

    if report["unknown_status"]:
        total += len(report["unknown_status"])
        print(f"\nSTATUS WORD NOT RECOGNISED (map it in STATUS_WORDS or normalise the snapshot): "
              f"{len(report['unknown_status'])}")
        for row in report["unknown_status"]:
            print(f"  - {row['name']}: {row['status_raw']!r}")

    if not specs_known:
        print("\nNOTE the expected source carries no spec names, so the structure dimension was skipped.")

    extras = report["extras"]
    if extras["count"]:
        print(f"\nextras: {extras['count']} snapshot rows are not in the expected list "
              f"({'--show-extras to list them' if not show_extras else 'listed below'})")
        if show_extras:
            for row in extras["items"]:
                print(f"  - {row['name']}  board={fmt(row['board'])}  price={money(row['price'])} "
                      f"{row['status']}  id={fmt(row['id'])} 建于 {row['created']}")

    if verbose:
        print("\nnormalised names (first 10):")
        seen = set()
        for row in expected:
            norm = normalize_name(row["name"])
            if norm not in seen:
                seen.add(norm)
                print(f"  {row['name']} -> {norm}")

    if total:
        print(f"\nRESULT: {total} differences")
        return 1
    print("\nRESULT: plan and shop agree")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expected", required=True, help="queue.json or the registration workbook")
    ap.add_argument("--listings", action="append", required=True, help="snapshot file; repeat per board")
    ap.add_argument("--expected-format", choices=("auto", "queue", "sheet"), default="auto")
    ap.add_argument("--sheet", default="Sheet1", help="workbook sheet for a .xlsx expected source")
    ap.add_argument("--cols", default="D,E,F,G,H", help="name,retail,coupon,supply,cost columns")
    ap.add_argument("--board", help="board tag for rows that do not carry one (e.g. pickup, express)")
    ap.add_argument("--price-tolerance", type=float, default=0.005,
                    help="how far a price may move and still count as the same (default 0.005)")
    ap.add_argument("--out", help="write the machine-readable diff here")
    ap.add_argument("--show-extras", action="store_true", help="list snapshot rows missing from the plan")
    ap.add_argument("--verbose", action="store_true", help="also print the name normalisation result")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    try:
        expected = load_expected(args.expected, args.expected_format, args.sheet, args.cols, args.board)
        specs_known = any(e["specs"] for e in expected)
        listings = load_listings(args.listings, args.board)
    except InputError as exc:
        print(f"cannot reconcile: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"cannot reconcile: {exc}", file=sys.stderr)
        return 2

    for row in expected:
        row["norm"] = normalize_name(row["name"])

    report = reconcile(expected, listings, args.price_tolerance)
    code = print_report(report, expected, listings, specs_known, args.show_extras, args.verbose)
    if args.out:
        try:
            Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            print(f"cannot write the diff to {args.out}: {exc}", file=sys.stderr)
            return 2
        print(f"diff written: {args.out}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
