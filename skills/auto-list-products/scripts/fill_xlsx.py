#!/usr/bin/env python3
"""Append product rows to a registration workbook without breaking its formulas.

Layout expected (1-based columns, override with --cols):
  D 产品名称 | E 售价 | F 送券 (formula) | G 供货价 (numeric + case-pack format) | H 单件成本 (formula)

Rules that matter
  * G must stay NUMERIC. The case pack is shown with number_format
    `0.##"（箱*24瓶）"`; turning the cell into text breaks every formula that
    references it (#VALUE!).
  * F and H stay formulas so price edits keep the sheet consistent, and the workbook is
    flagged for a full recalc on open (programmatically written formulas have no cache).
  * Rows without a supply price (single-spec items, or a price the supplier has not given
    yet) write name + retail + a literal coupon and leave the supply/cost cells empty.
  * Empty rows in these workbooks often carry leftover template styling (red font, no
    border). Every written cell inherits the border of the row above and is set to black.
  * The workbook is backed up before writing and refused while Excel holds the lock.

rows.json (list):
  {"name": "示例商品500ml", "retail": 3.0, "supply": 31.5, "per": 15, "unit": "瓶", "cu": "箱"}
  {"name": "单品商品", "retail": 238, "coupon": 88}          # no supply price

Usage
  python fill_xlsx.py --xlsx sheet.xlsx --sheet 登记表 --rows rows.json
  python fill_xlsx.py ... --start 52          # force the first row instead of detecting it

Exit codes: 0 ok, 1 refused (locked / missing / no rows).
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import shutil
import sys

import openpyxl
from openpyxl.styles import Alignment, Font


def last_filled_row(ws, name_col: str) -> int:
    """Last row with a product name - ws.max_row lies, these sheets end in styled empty rows."""
    last = 0
    for cell in ws[name_col]:
        if cell.value not in (None, ""):
            last = cell.row
    return last


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--start", type=int, help="first row to write (default: after the last filled name cell)")
    ap.add_argument("--backup-dir", default=None, help="default: alongside the workbook")
    ap.add_argument("--cols", default="D,E,F,G,H", help="name,retail,coupon,supply,cost columns")
    args = ap.parse_args()

    lock = os.path.join(os.path.dirname(args.xlsx) or ".", "~$" + os.path.basename(args.xlsx))
    if os.path.exists(lock):
        print("refused: workbook is open in Excel, close it first", file=sys.stderr)
        return 1

    rows = json.load(open(args.rows, encoding="utf-8"))
    if not rows:
        print("refused: no rows", file=sys.stderr)
        return 1

    name_col, retail_col, coupon_col, supply_col, cost_col = [c.strip() for c in args.cols.split(",")]
    wb = openpyxl.load_workbook(args.xlsx)
    if args.sheet not in wb.sheetnames:
        print(f"refused: sheet {args.sheet!r} not in {wb.sheetnames}", file=sys.stderr)
        return 1
    ws = wb[args.sheet]

    start = args.start or (last_filled_row(ws, name_col) + 1)
    if start < 2:
        print("refused: sheet has no header row / no data to append after", file=sys.stderr)
        return 1
    if any(ws[f"{name_col}{r}"].value not in (None, "") for r in range(start, ws.max_row + 1)):
        print(f"refused: rows from {start} down are not empty - pass --start at the real first empty row",
              file=sys.stderr)
        return 1

    backup_dir = args.backup_dir or os.path.dirname(args.xlsx) or "."
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = os.path.splitext(os.path.basename(args.xlsx))[0]
    backup = os.path.join(backup_dir, f"{stem}.{stamp}.xlsx")
    shutil.copy2(args.xlsx, backup)

    has_formulas = False
    written = []
    row = start
    for item in rows:
        name = item.get("name")
        retail = item.get("retail")
        if not name or retail in (None, ""):
            print(f"refused: row entry needs name and retail: {item}", file=sys.stderr)
            return 1
        supply, per = item.get("supply"), item.get("per")
        if supply is not None and not per:
            print(f"refused: {name} has a supply price but no `per` (units per case)", file=sys.stderr)
            return 1
        if supply is None and item.get("coupon") in (None, ""):
            print(f"refused: {name} has no supply price and no coupon - one of them is needed", file=sys.stderr)
            return 1

        plan = {name_col: (name, "General", False)}
        plan[retail_col] = (float(retail), "0.00", False)
        if supply is not None:
            cu, unit = item.get("cu"), item.get("unit")
            fit = f'0.##"（{cu}*{per}{unit}）"' if cu and unit else "0.00"
            plan[coupon_col] = (f"=ROUND({retail_col}{row}-{cost_col}{row},2)", "0.00", True)
            plan[supply_col] = (float(supply), fit, False)
            plan[cost_col] = (f"=ROUND({supply_col}{row}/{per},2)", "0.00", True)
            coupon_preview = round(float(retail) - float(supply) / int(per), 2)
        else:
            plan[coupon_col] = (float(item["coupon"]), "0.00", False)
            plan[supply_col] = (None, None, False)
            plan[cost_col] = (None, None, False)
            coupon_preview = float(item["coupon"])

        for col, (value, fmt, formula) in plan.items():
            cell = ws[f"{col}{row}"]
            inherit = ws[f"{col}{row - 1}"]
            if value is not None:
                cell.value = value
            if fmt:
                cell.number_format = fmt
            cell.alignment = Alignment(vertical="center", wrap_text=(col == name_col))
            cell.border = copy.copy(inherit.border)
            cell.font = Font(name=cell.font.name, size=cell.font.sz or 12,
                             bold=cell.font.bold, color="FF000000")
            has_formulas = has_formulas or formula

        written.append((row, name, coupon_preview))
        row += 1

    if has_formulas:
        wb.calculation.fullCalcOnLoad = True

    wb.save(args.xlsx)
    print(f"backup: {backup}")
    print(f"wrote rows {start}-{row - 1} ({len(written)} products)")
    for r, name, coupon in written:
        print(f"  {r}  {name}  coupon={coupon}")
    if has_formulas:
        print("reminder: coupon/cost are formulas - reopen the workbook or press F9 to see values")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
