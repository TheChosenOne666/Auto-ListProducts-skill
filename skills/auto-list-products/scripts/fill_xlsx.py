#!/usr/bin/env python3
"""Append product rows to a registration workbook without breaking its formulas.

Layout expected (1-based columns, override with --cols):
  D 产品名称 | E 售价 | F 送券 (formula) | G 供货价 (numeric + case-pack format) | H 单件成本 (formula)

Rules that matter
  * G must stay NUMERIC. The case pack is shown with number_format
    `0.##"（箱*24瓶）"`; turning the cell into text breaks every formula that
    references it (#VALUE!).
  * F and H stay formulas so price edits keep the sheet consistent.
  * The workbook is backed up before writing and refused while Excel holds the lock.

rows.json (list):
  {"name": "示例商品500ml", "retail": 3.0, "supply": 31.5, "per": 15, "unit": "瓶", "cu": "箱"}

Usage
  python fill_xlsx.py --xlsx sheet.xlsx --sheet 登记表 --rows rows.json --start 52
  python fill_xlsx.py ... --normalize-font      # reset inherited red font on written cells

Exit codes: 0 ok, 1 refused (locked / missing), 2 partial.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys

import openpyxl
from openpyxl.styles import Alignment, Font


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--start", type=int, required=True, help="first empty data row (1-based)")
    ap.add_argument("--backup-dir", default=None, help="default: alongside the workbook")
    ap.add_argument("--cols", default="D,E,F,G,H", help="name,retail,coupon,supply,cost columns")
    ap.add_argument("--normalize-font", action="store_true", help="force black font on written cells")
    args = ap.parse_args()

    lock = os.path.join(os.path.dirname(args.xlsx), "~$" + os.path.basename(args.xlsx))
    if os.path.exists(lock):
        print("refused: workbook is open in Excel, close it first", file=sys.stderr)
        return 1

    rows = json.load(open(args.rows, encoding="utf-8"))
    if not rows:
        print("refused: no rows", file=sys.stderr)
        return 1

    backup_dir = args.backup_dir or os.path.dirname(args.xlsx)
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(backup_dir, f"{os.path.splitext(os.path.basename(args.xlsx))[0]}.{stamp}.xlsx")
    shutil.copy2(args.xlsx, backup)

    name_col, retail_col, coupon_col, supply_col, cost_col = [c.strip() for c in args.cols.split(",")]
    wb = openpyxl.load_workbook(args.xlsx)
    ws = wb[args.sheet]

    row = args.start
    written = []
    for item in rows:
        per = int(item["per"])
        supply = float(item["supply"])
        retail = float(item["retail"])
        fit = f'0.##"（{item.get("cu", "箱")}*{per}{item.get("unit", "件")}）"'
        cells = {
            name_col: (item["name"], "General"),
            retail_col: (retail, "0.00"),
            coupon_col: (f"=ROUND({retail_col}{row}-{cost_col}{row},2)", "0.00"),
            supply_col: (supply, fit),
            cost_col: (f"=ROUND({supply_col}{row}/{per},2)", "0.00"),
        }
        for col, (value, fmt) in cells.items():
            cell = ws[f"{col}{row}"]
            cell.value = value
            cell.number_format = fmt
            cell.alignment = Alignment(vertical="center", wrap_text=(col == name_col))
            if args.normalize_font:
                cell.font = Font(name=cell.font.name, size=cell.font.sz or 12,
                                 bold=cell.font.bold, color="FF000000")
        written.append((row, item["name"], round(retail - supply / per, 2)))
        row += 1

    wb.save(args.xlsx)
    print(f"backup: {backup}")
    print(f"wrote rows {args.start}-{row - 1} ({len(written)} products)")
    for r, name, coupon in written:
        print(f"  {r}  {name}  coupon={coupon}")
    print("reminder: F/H are formulas - reopen the workbook or press F9 to see values")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
