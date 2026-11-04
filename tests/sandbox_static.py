#!/usr/bin/env python3
"""Static checks for the practice sandbox - no browser, so CI can run them anywhere.

The sandbox is a fixture: its value is that a rehearsal and a reconcile run against known truth.
These checks keep it honest offline - no network calls, a deterministic seed, the snapshot it
renders really matches listings.example.json, and every trap the README promises is actually in
the code (an undocumented trap would make a comparison run irreproducible, and a missing one would
make the rehearsal weaker than the README claims).

Usage
  python tests/sandbox_static.py          # exit 0 clean, 1 with failures printed
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "sandbox" / "admin.html"
README = ROOT / "sandbox" / "README.md"
LISTINGS = ROOT / "skills" / "auto-list-products" / "listings.example.json"

NETWORK_TOKENS = ["fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "import(",
                  "src=\"http", "href=\"http", "//cdn", "//unpkg", "//jsdelivr", "@import url"]

# every trap the README promises, paired with the code that has to implement it
TRAPS = {
    "重量不标必填但拦住提交": r"的重量为空",
    "同名类目节点": r'"食品/零食": \{[^}]*"食品": null',
    "速食食品与食品互相包含": r'"速食食品"',
    "主图必须上传": r"主图未上传",
    "提交需要二次确认": r"确认提交审核",
    "库存预填默认值": r'numInput\("stock", i, "10000"\)',
}

failures: list[str] = []


def check(cond, label):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


def main() -> int:
    html = HTML.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8") if README.exists() else ""

    check(HTML.exists(), "sandbox/admin.html exists")
    hits = [token for token in NETWORK_TOKENS if token in html]
    check(not hits, f"the page makes no network calls and loads no external asset (found {hits})")
    check("练习用" in html and "不是任何真实店铺" in html, "the page says out loud it is a practice sandbox")

    ids = re.findall(r'"(SPU\d+)"', html)
    check(len(ids) >= 6, f"the seed has {len(ids)} fixed SPU codes")
    check(all(re.fullmatch(r"SPU\d{18}", i) for i in ids),
          "every seed id is SPU + 18 digits, so the audit can read the built time out of it")
    check(len(set(ids)) == len(ids), "seed ids are unique")

    for label, pattern in TRAPS.items():
        check(re.search(pattern, html) is not None, f"trap present: {label}")
        check(label in readme, f"trap documented in sandbox/README.md: {label}")

    snapshot_keys = re.search(r"return \{\s*board:.*?total:.*?items:", html, re.S)
    check(snapshot_keys is not None, "the snapshot builder returns board/total/items")
    example = json.loads(LISTINGS.read_text(encoding="utf-8"))
    documented = {k for k in example if not k.startswith("_")}
    check({"board", "capturedAt", "source", "total", "items"} == documented,
          "listings.example.json documents exactly the keys the sandbox renders")
    item_keys = {k for k in example["items"][0] if not k.startswith("_")}
    check({"id", "name", "status", "specs"} <= item_keys, "snapshot rows carry id/name/status/specs")

    for route in ("#/new", "#/list", "#/recycle"):
        check(route in html, f"route present: {route}")
    check("window.__alpSnapshot" in html, "the snapshot is exposed for scripted read-back")

    print()
    if failures:
        print(f"{len(failures)} sandbox static-check failures")
        return 1
    print("sandbox static checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
