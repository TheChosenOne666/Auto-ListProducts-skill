#!/usr/bin/env python3
"""End-to-end smoke test for the scripts shipped with the auto-list-products skill.

Builds throwaway fixtures (screenshots, a profile, a workbook) in a temp folder and runs
every script the way the skill runs it, asserting on the produced artifacts. This is what
catches a script that compiles but does not work, and it runs on both Linux and Windows CI
because the real operator's machine is Windows.

Usage
  python tests/smoke.py            # exit 0 = all good, 1 = first failure printed

Requires pillow and openpyxl only.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Border, Font, Side

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "auto-list-products" / "scripts"
EXAMPLE_PROFILE = ROOT / "skills" / "auto-list-products" / "profile.example.json"

failures: list[str] = []


def check(cond, label):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)
    return cond


def run(script: str, *args) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPTS / script), *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def noise(im: Image.Image, x0: int, y0: int, x1: int, y1: int) -> None:
    """Paste a busy band that is unmistakably content (dense, dark-ish, many colours)."""
    size = (x1 - x0, y1 - y0)
    band = Image.merge("RGB", [Image.effect_noise(size, s) for s in (60, 90, 40)])
    im.paste(band, (x0, y0))


def make_shots(shots: Path) -> None:
    """A clean 1440x3200 screenshot, a blank one, and one whose window catches carousel dots."""
    good = Image.new("RGB", (1440, 3200), "white")
    noise(good, 0, 285, 1440, 1725)
    good.save(shots / "good.png")

    blank = Image.new("RGB", (1440, 3200), "white")
    blank.save(shots / "blank.png")

    dotted = Image.new("RGB", (1440, 3200), "white")
    noise(dotted, 0, 285, 1440, 1560)
    draw = ImageDraw.Draw(dotted)
    for i in range(3):
        draw.ellipse([300 + i * 90, 1640, 340 + i * 90, 1680], fill="black")
    dotted.save(shots / "dotted.png")


def make_profile(tmp: Path) -> Path:
    profile = json.loads(EXAMPLE_PROFILE.read_text(encoding="utf-8"))

    def fill(node):
        if isinstance(node, dict):
            return {k: fill(v) for k, v in node.items()}
        if isinstance(node, list):
            return [fill(v) for v in node]
        if isinstance(node, str) and node.startswith("<") and node.endswith(">"):
            return "x" + node.strip("<>").replace(" ", "_")[:24]
        return node

    profile = fill(profile)
    profile["platform"] = "smoke"
    profile["form"]["newUrl"] = "https://admin.test/new"
    profile["form"]["listUrl"] = "https://admin.test/list"
    profile["fields"]["category"]["expectedSeparator"] = " / "
    profile["fields"]["spec"]["caseSkuLabel"] = "{per}{unit}*{cu}"
    profile["fields"]["spec"]["skuFieldOrder"] = ["price", "stock", "points"]
    profile["defaults"]["rootCategory"] = "RootCat"
    profile["defaults"]["specName"] = "SpecName"
    profile["defaults"]["stock"] = 10000
    profile["business"] = {"mode": "fill-and-submit", "pointsMode": "coupon",
                           "minCoupon": 0, "counters": True, "caseSku": True}
    profile["notes"] = []
    path = tmp / "profile.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def test_profile(tmp: Path) -> Path:
    res = run("check_profile.py", EXAMPLE_PROFILE)
    check(res.returncode == 0,
          "check_profile prints the shipped example, whose placeholders are Chinese, and survives the console codepage")
    profile = make_profile(tmp)
    res = run("check_profile.py", profile)
    check(res.returncode == 0, f"check_profile accepts the filled example (stderr={res.stderr[:120]})")
    check("upload trays" in res.stdout, "check_profile summarises the upload trays")

    broken = json.loads(profile.read_text(encoding="utf-8"))
    broken["image"]["trays"] = []
    broken["business"]["pointsMode"] = "maybe"
    broken["fields"]["spec"]["skuFieldOrder"] = ["stock"]
    bad_path = tmp / "profile-broken.json"
    bad_path.write_text(json.dumps(broken), encoding="utf-8")
    res = run("check_profile.py", bad_path, "--quiet")
    out = res.stdout
    check(res.returncode == 1, "check_profile refuses an empty tray list")
    check("image.trays" in out, "the refusal names image.trays")
    check("pointsMode" in out, "the refusal names the bad pointsMode")
    check("'price'" in out, "the refusal names the missing price in skuFieldOrder")
    return profile


def test_crop(tmp: Path) -> None:
    shots = tmp / "shots"
    shots.mkdir(exist_ok=True)
    make_shots(shots)
    products = [
        {"n": 1, "name": "clean", "img": "clean.jpg", "src": "good.png"},
        {"n": 2, "name": "rescued", "img": "rescued.jpg", "src": "blank.png", "alt_src": "good.png"},
        {"n": 3, "name": "dotty", "img": "dotty.jpg", "src": "dotted.png"},
    ]
    products_path = tmp / "products_crop.json"
    products_path.write_text(json.dumps(products), encoding="utf-8")
    crops = tmp / "crops"
    report_path = tmp / "crop-report.json"

    res = run("crop_square.py", "--src", shots, "--dst", crops, "--products", products_path,
              "--windows", "1440x3200:285", "--size", "512", "--sheet", tmp / "sheet.png",
              "--report", report_path)
    check(res.returncode == 0, f"crop_square exits 0 with a usable alt source (out={res.stdout[:160]})")
    report = json.loads(report_path.read_text(encoding="utf-8"))["items"]
    by_name = {r["name"]: r for r in report}
    check(by_name["clean"]["status"] == "ok", "clean crop reported ok")
    check(by_name["rescued"].get("attempt") == 2 and by_name["rescued"]["src"] == "good.png",
          "blank source falls back to alt_src instead of writing white")
    check(by_name["dotty"]["status"].startswith("dots-capped"), "carousel dots are capped off")
    check((crops / "clean.jpg").exists() and (crops / "rescued.jpg").exists(), "crops written")
    check((tmp / "sheet.png").exists(), "contact sheet written")
    check(all(r["bytes"] > 5000 for r in report if "bytes" in r), "crops carry real content")

    dead = tmp / "dead.json"
    dead.write_text(json.dumps([{"n": 9, "name": "hopeless", "img": "h.jpg", "src": "blank.png"}]), encoding="utf-8")
    res = run("crop_square.py", "--src", shots, "--dst", tmp / "crops2", "--products", dead,
              "--windows", "1440x3200:285", "--size", "512")
    check(res.returncode == 2, "an all-blank batch exits 2")
    check(not (tmp / "crops2" / "h.jpg").exists(), "a blank crop is never written to disk")

    res = run("crop_square.py", "--src", shots, "--dst", crops, "--products", products_path,
              "--windows", "1440x3200:285:1500", "--size", "512")
    check(res.returncode == 0 and "padded" in res.stdout, "an explicit bottom bound pads back to square")


def test_images(tmp: Path) -> None:
    crops = tmp / "crops"
    res = run("check_images.py", "--dir", crops, "--min-bytes", "5000", "--min-colors", "200")
    check(res.returncode == 0, f"check_images passes the crops (out={res.stdout[:160]})")
    check("clean.jpg" in res.stdout, "jpg crops are scanned")

    mixed = tmp / "mixed"
    mixed.mkdir(exist_ok=True)
    Image.new("RGB", (400, 400), "white").save(mixed / "white.png")
    noise_res = Image.new("RGB", (400, 400), "white")
    noise(noise_res, 0, 0, 400, 400)
    noise_res.save(mixed / "busy.png")
    res = run("check_images.py", "--dir", mixed, "--min-bytes", "1", "--min-colors", "50")
    check(res.returncode == 1 and "white.png" in res.stdout and "busy.png" in res.stdout,
          "png-only folder is scanned (no *.jpg glob blind spot)")

    urls = tmp / "urls.txt"
    urls.write_text("https://invalid.host.test/nope.png\n", encoding="utf-8")
    res = run("check_images.py", "--urls", urls)
    check(res.returncode == 1 and "ERR" in res.stdout, "an unreachable hosted image is reported as a suspect")


def test_queue(tmp: Path, profile: Path) -> None:
    products = [
        {"n": 1, "name": "Item A", "size": "500ml", "unit": "bottle", "per": 15, "cu": "箱",
         "supply": 31.5, "retail": 3.0, "cat": "TEA", "img": "clean.jpg"},
        {"n": 2, "name": "Item B", "size": "330ml", "unit": "can", "per": 24, "cu": "case",
         "supply": 60, "retail": 3.5, "cat": "SODA", "img": "rescued.jpg", "stock": 50},
        {"n": 3, "name": "Item A", "size": "500ml", "unit": "bottle", "per": 15, "cu": "case",
         "supply": 31.5, "retail": 3.0, "cat": "TEA", "img": "clean.jpg"},
    ]
    products_path = tmp / "products.json"
    products_path.write_text(json.dumps(products, ensure_ascii=False), encoding="utf-8")
    cats = tmp / "categories.json"
    cats.write_text(json.dumps({"TEA": {"l2": "Drink", "l3": "Tea"}, "SODA": {"l2": "Drink", "l3": "Soda"}}),
                    encoding="utf-8")

    out = tmp / "queue.json"
    res = run("make_queue.py", "--profile", profile, "--products", products_path,
              "--categories", cats, "--out", out)
    check(res.returncode == 0, f"make_queue builds from the profile (out={res.stdout[:200]})")
    queue = json.loads(out.read_text(encoding="utf-8"))
    check(len(queue) == 2, "same name + same retail is dropped as a duplicate")
    check("duplicates dropped" in res.stdout, "the dropped duplicate is reported")
    a = queue[0]
    check(a["expectCat"] == "RootCat / Drink / Tea", "expectCat uses the profile separator and root")
    check(a["v2"] == "15bottle*箱", "case label comes from the profile template, in the user's language")
    check(a["stock"] == 10000 and queue[1]["stock"] == 50, "stock comes from the profile, per-product wins")
    check(a["specName"] == "SpecName", "specName comes from the profile")
    check(a["pt1"] == 0.9 and a["pt2"] == 13.5, "points derived from the coupon per pointsMode")
    check("sales" in a and "fav" in a, "display counters emitted per profile")

    res = run("make_queue.py", "--profile", profile, "--products", products_path, "--categories", cats,
              "--out", tmp / "q2.json", "--no-case-sku", "--points", "none", "--root", "Other", "--board", "pickup")
    check(res.returncode == 0, "flags override the profile")
    q2 = json.loads((tmp / "q2.json").read_text(encoding="utf-8"))
    check("v2" not in q2[0] and "pt1" not in q2[0], "overrides removed the case row and points")
    check(q2[0]["board"] == "pickup", "--board tags every item for the audit")
    check(q2[0]["sales"] == a["sales"], "counters still come from the profile")
    check(q2[0]["expectCat"].startswith("Other / "), "--root override applied")

    broken = json.loads(products_path.read_text(encoding="utf-8"))
    broken.append({"n": 4, "name": "No Cat", "size": "1L", "unit": "bottle", "per": 6,
                   "supply": 3, "retail": 9, "cat": "ZZZ", "img": "x.jpg"})
    broken_path = tmp / "products-broken.json"
    broken_path.write_text(json.dumps(broken), encoding="utf-8")
    res = run("make_queue.py", "--profile", profile, "--products", broken_path, "--categories", cats,
              "--out", tmp / "q3.json")
    check(res.returncode == 1 and "NO-CATEGORY" in res.stdout, "unknown category key aborts the queue")
    check(not (tmp / "q3.json").exists(), "a rejected run writes no queue")

    negative = json.loads(products_path.read_text(encoding="utf-8"))
    negative[0]["supply"] = 99.0
    negative_path = tmp / "products-negative.json"
    negative_path.write_text(json.dumps(negative), encoding="utf-8")
    res = run("make_queue.py", "--profile", profile, "--products", negative_path, "--categories", cats,
              "--out", tmp / "q4.json")
    check(res.returncode == 1 and "SUSPECT" in res.stdout, "a negative coupon aborts with the numbers printed")

    # the two things the sandbox rehearsal could not do: a retail-only batch, and a two-level path
    retail_only = queue_file(tmp, "q12.json", [{"n": 30, "name": "Retail Only", "size": "500ml",
                                                "unit": "bottle", "retail": 3.0, "cat": "SODA", "img": "r.jpg"}])
    shallow_cats = tmp / "categories-shallow.json"
    shallow_cats.write_text(json.dumps({"SODA": {"l2": "Drink"}}, ensure_ascii=False), encoding="utf-8")
    res = run("make_queue.py", "--profile", profile, "--products", retail_only, "--categories", shallow_cats,
              "--out", tmp / "q12-out.json", "--no-case-sku", "--points", "none")
    check(res.returncode == 0, "a retail-only batch builds a queue when nothing consumes the cost")
    built = json.loads((tmp / "q12-out.json").read_text(encoding="utf-8"))
    check("coupon" not in built[0] and "v2" not in built[0], "no coupon or case row is invented for cost-free data")
    check(built[0]["expectCat"] == "RootCat / Drink" and "l3" not in built[0],
          "a two-level category path is legal, not an unknown key")
    res = run("make_queue.py", "--profile", profile, "--products", retail_only, "--categories", shallow_cats,
              "--out", tmp / "q12b.json")
    check(res.returncode == 1 and "supply/per are only needed" in res.stdout,
          "when the profile does need cost, the refusal says why instead of only naming missing keys")
    varying_root = tmp / "categories-varying.json"
    varying_root.write_text(json.dumps({"SODA": {"l1": "Drinks", "l2": "Soda"}}, ensure_ascii=False),
                            encoding="utf-8")
    res = run("make_queue.py", "--profile", profile, "--products", retail_only, "--categories", varying_root,
              "--out", tmp / "q13.json", "--no-case-sku", "--points", "none")
    built = json.loads((tmp / "q13.json").read_text(encoding="utf-8"))
    check(res.returncode == 0 and built[0]["expectCat"] == "Drinks / Soda",
          "a catalogue whose first level varies overrides the root per key")


def test_xlsx(tmp: Path) -> None:
    path = tmp / "sheet.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "登记"
    thin = Side(style="thin")
    for col, value in zip("DEFGH", ["产品名称", "售价", "送券", "供货价", "单件成本"]):
        ws[f"{col}1"] = value
    for row in (2, 3):
        for col, value in zip("DEFGH", ["Old", 10, "=ROUND(E2-H2,2)", 60, "=ROUND(G2/15,2)"]):
            cell = ws[f"{col}{row}"]
            cell.value = value
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for col in "DEFGH":  # template leftovers: red font, no border
        ws[f"{col}4"].font = Font(color="FFFF0000")
        ws[f"{col}5"].font = Font(color="FFFF0000")
    wb.save(path)

    rows = tmp / "rows.json"
    rows.write_text(json.dumps([
        {"name": "示例商品 500ml", "retail": 3.0, "supply": 31.5, "per": 15, "unit": "瓶", "cu": "箱"},
        {"name": "Whole item", "retail": 238, "coupon": 88},
    ], ensure_ascii=False), encoding="utf-8")

    res = run("fill_xlsx.py", "--xlsx", path, "--sheet", "登记", "--rows", rows)
    check(res.returncode == 0, f"fill_xlsx appends (out={res.stdout[:200]}, err={res.stderr[:160]})")

    after = load_workbook(path)
    ws = after["登记"]
    check(ws["D4"].value == "示例商品 500ml" and ws["E4"].value == 3.0, "auto-detected the first empty row")
    check(str(ws["F4"].value).startswith("=ROUND(E4-H4"), "coupon stays a formula")
    check(ws["G4"].value == 31.5 and isinstance(ws["G4"].value, float), "supply stays numeric")
    check("箱*15瓶" in ws["G4"].number_format, "case pack shown via number_format")
    check(str(ws["H4"].value).startswith("=ROUND(G4/15"), "unit cost stays a formula")
    check(ws["F5"].value == 88 and ws["G5"].value is None and ws["H5"].value is None,
          "no-supply row writes a literal coupon and leaves supply/cost empty")
    check(ws["D4"].font.color.rgb == "FF000000" and ws["D5"].font.color.rgb == "FF000000",
          "red template font replaced with black")
    check(ws["D4"].border.left.style == "thin", "border inherited from the previous row")
    check(after.calculation.fullCalcOnLoad is True, "workbook flagged for recalc on open")

    res = run("fill_xlsx.py", "--xlsx", path, "--sheet", "登记", "--rows", rows, "--start", 2)
    check(res.returncode == 1, "refuses to overwrite filled rows")

    res = run("fill_xlsx.py", "--xlsx", path, "--sheet", "nope", "--rows", rows)
    check(res.returncode == 1 and "not in" in res.stderr, "refuses an unknown sheet name")


def rec(*args) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPTS / "reconcile.py"), *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def load_reconcile():
    """Import reconcile.py by path so the pure rules can be asserted without a subprocess."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("reconcile", SCRIPTS / "reconcile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(name, price, specs=None, status="on-sale", board=None, ident=None):
    return {"id": ident or f"SPU20260101120000{abs(hash(name)) % 1000:03d}", "name": name, "status": status,
            "board": board, "specs": specs if specs is not None else [{"name": f"{name} size", "price": price}]}


def want(n, name, price, v1=None, v2=None, pt1=None, board=None):
    item = {"n": n, "name": name, "price1": price}
    if v1:
        item["v1"] = v1
    if v2:
        item["v2"] = v2
    if pt1 is not None:
        item["pt1"] = pt1
    if board:
        item["board"] = board
    return item


def snapshot(tmp: str, name: str, items, total=None, board=None) -> Path:
    path = tmp / name
    body = {"items": items}
    if board:
        body["board"] = board
    if total is not None:
        body["total"] = total
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def queue_file(tmp: str, name: str, items) -> Path:
    path = tmp / name
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return path


def test_reconcile(tmp: Path) -> None:
    folder = tmp / "rec"
    folder.mkdir(exist_ok=True)
    tmp = folder

    module = load_reconcile()
    norm = module.normalize_name
    check(norm("示例商品 330ml（箱*24罐）") == norm("示例商品330ml"),
          "case-pack suffix in full-width brackets normalises away")
    check(norm("Item (1*24罐)") == norm("Item") and norm("Item*24罐") == norm("Item"),
          "half-width and bare-suffix pack notations normalise the same")
    check(norm("盐焗鹅(2000g)") == "盐焗鹅2000g" and norm("卤鹅(2000g)") == "卤鹅2000g",
          "a parenthesised size that is not pack notation is kept, so 盐焗鹅 and 卤鹅 stay different products")
    check(norm("A (箱*24瓶) 500ml") != norm("A 330ml"), "normalisation does not collapse different products")

    source = (SCRIPTS / "reconcile.py").read_text(encoding="utf-8")
    check(not any(tok in source for tok in ("urllib", "socket", "requests", "http.client")),
          "reconcile reads files only - no network module in the source")
    check(module.created_from_id("SPU202610241058190914") == "2026-10-24 10:58",
          "the creation time is read out of the SPU code")

    # 1. plan and shop agree
    items = [row("A 500ml", 3.0, [{"name": "500ml*1瓶", "price": 3.0, "points": 0.8},
                                  {"name": "15瓶*箱", "price": 45.0}])]
    q = queue_file(tmp, "q1.json", [want(1, "A 500ml", 3.0, "500ml*1瓶", "15瓶*箱", 0.8)])
    s = snapshot(tmp, "l1.json", items, total=1, board="pickup")
    res = rec("--expected", q, "--listings", s, "--board", "pickup")
    check(res.returncode == 0 and "plan and shop agree" in res.stdout, "an exact match exits 0")

    # 2. missing product
    res = rec("--expected", queue_file(tmp, "q2.json", [want(2, "Ghost", 9.9, "1L*1瓶")]), "--listings", s, "--board", "pickup")
    check(res.returncode == 1 and "MISSING - in the plan, not in the shop: 1" in res.stdout and "Ghost" in res.stdout,
          "a product absent from the shop is reported as MISSING with its name")

    # 3. price drift
    drifted = [row("A 500ml", 3.0, [{"name": "500ml*1瓶", "price": 3.0}], ident="SPU202601020930112233")]
    res = rec("--expected", queue_file(tmp, "q3.json", [want(3, "A 500ml", 3.5, "500ml*1瓶")]),
              "--listings", snapshot(tmp, "l3.json", drifted, board="pickup"), "--board", "pickup")
    check(res.returncode == 1 and "期望 3.5" in res.stdout and "实际 3.0" in res.stdout
          and "建于 2026-01-02 09:30" in res.stdout, "a price change that never reached the shop is priced, with the built time")

    # 4. case-only listing (the real shape of the three milk products that shipped without a single unit)
    case_only = [row("M 250ml", 72.0, [{"name": "24盒*箱", "price": 72.0}], ident="SPU202610241058190914")]
    res = rec("--expected", queue_file(tmp, "q4.json", [want(4, "M 250ml", 3.0, "250ml*1盒", "24盒*箱")]),
              "--listings", snapshot(tmp, "l4.json", case_only, board="pickup"), "--board", "pickup")
    check("PRICE" in res.stdout and "实际 72" in res.stdout and "MISSING - in the plan, not in the shop: 0" in res.stdout,
          "a listing that only has the case row surfaces as a price problem, not as a missing product")
    res = rec("--expected", queue_file(tmp, "q4b.json", [want(4, "M 250ml", 3.0, "250ml*1盒", "12盒*箱")]),
              "--listings", snapshot(tmp, "l4b.json", [row("M 250ml", 3.0, [{"name": "250ml*1盒", "price": 3.0},
                                                                           {"name": "24盒*箱", "price": 60.0}])],
                                     board="pickup"), "--board", "pickup")
    check("STRUCTURE" in res.stdout and "缺规格 ['12盒*箱']" in res.stdout,
          "same price but the wrong spec rows is a structure mismatch")

    # 5. same name, different price is not a duplicate
    pair = [row("T 500ml", 3.0), row("T 500ml", 3.5)]
    qpair = queue_file(tmp, "q5.json", [want(5, "T 500ml", 3.0, "T 500ml size"), want(6, "T 500ml", 3.5, "T 500ml size")])
    res = rec("--expected", qpair, "--listings", snapshot(tmp, "l5.json", pair, board="pickup"), "--board", "pickup")
    check(res.returncode == 0 and "DUPLICATE - same name and price listed more than once: 0" in res.stdout,
          "two products sharing a name at different prices are both accepted")

    # 6. same name, same price twice is a duplicate
    res = rec("--expected", queue_file(tmp, "q6.json", [want(7, "T 500ml", 3.0, "T 500ml size")]),
              "--listings", snapshot(tmp, "l6.json", [row("T 500ml", 3.0), row("T 500ml", 3.0)], board="pickup"),
              "--board", "pickup")
    check("DUPLICATE - same name and price listed more than once: 1" in res.stdout and "x2" in res.stdout,
          "the same name at the same price listed twice is a duplicate")

    # 7. suffix writing differences do not create fake gaps
    res = rec("--expected", queue_file(tmp, "q7.json", [want(8, "示例商品 330ml", 3.0, "330ml*1罐")]),
              "--listings", snapshot(tmp, "l7.json", [row("示例商品330ml（箱*24罐）", 3.0, [{"name": "330ml*1罐", "price": 3.0}])],
                                     board="pickup"), "--board", "pickup")
    check(res.returncode == 0, "the same product written with or without a case-pack suffix reconciles")

    # 8. boards stay apart
    express = snapshot(tmp, "l-express.json", [row("Only In Express", 20.0)], board="express")
    res = rec("--expected", queue_file(tmp, "q8.json", [want(9, "Only In Express", 20.0)]),
              "--listings", express, "--board", "pickup")
    check("MISSING - in the plan, not in the shop: 1" in res.stdout and "extras: 1" in res.stdout,
          "a pickup expectation is not satisfied by an express row - boards do not leak")
    both = snapshot(tmp, "l-both.json", [row("P Item", 3.0, board="pickup"), row("E Item", 30.0, board="express")])
    res = rec("--expected", queue_file(tmp, "q8b.json", [want(10, "P Item", 3.0, board="pickup"),
                                                        want(11, "E Item", 30.0, board="express")]),
              "--listings", both)
    check(res.returncode == 0, "row-level board tags reconcile each board on its own")

    # 9. status is reported, not counted as missing
    pending = [row("A 500ml", 3.0, [{"name": "500ml*1瓶", "price": 3.0}], status="待审核")]
    res = rec("--expected", queue_file(tmp, "q9.json", [want(12, "A 500ml", 3.0, "500ml*1瓶")]),
              "--listings", snapshot(tmp, "l9.json", pending, board="pickup"), "--board", "pickup")
    check("MISSING - in the plan, not in the shop: 0" in res.stdout and "STATUS - found, but not live on the shelf: 1" in res.stdout
          and "待审核" in res.stdout, "a pending-review row is named as a status, never re-listed as missing")
    weird = [row("A 500ml", 3.0, [{"name": "500ml*1瓶", "price": 3.0}], status="库存异常")]
    res = rec("--expected", queue_file(tmp, "q9b.json", [want(13, "A 500ml", 3.0, "500ml*1瓶")]),
              "--listings", snapshot(tmp, "l9b.json", weird, board="pickup"), "--board", "pickup")
    check("STATUS WORD NOT RECOGNISED" in res.stdout, "an unknown status wording is reported instead of guessed")

    # 10. unusable input is refused loudly
    bad = tmp / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    res = rec("--expected", bad, "--listings", s)
    check(res.returncode == 2 and "cannot reconcile" in res.stderr, "invalid JSON is refused with exit 2")
    res = rec("--expected", queue_file(tmp, "q10.json", [want(14, "A", 1.0)]),
              "--listings", snapshot(tmp, "l10.json", [row("A", 1.0)], total=999))
    check(res.returncode == 2 and "a page is missing" in res.stderr, "a snapshot whose total disagrees with its rows is refused")
    res = rec("--expected", str(tmp / "nope.txt"), "--listings", s)
    check(res.returncode == 2, "an unrecognised expected format is refused")
    res = rec("--expected", queue_file(tmp, "q10b.json", [{"name": "no price"}]), "--listings", s)
    check(res.returncode == 2 and "name and price1" in res.stderr, "a queue item without price1 is refused, not skipped")

    # 11. scale
    big_want = [want(i, f"Bulk {i}", 1.0 + i, f"{i}ml*1瓶") for i in range(1, 201)]
    big_rows = [row(f"Bulk {i}", 1.0 + i, [{"name": f"{i}ml*1瓶", "price": 1.0 + i}]) for i in range(1, 701)]
    import time

    start = time.time()
    res = rec("--expected", queue_file(tmp, "q11.json", big_want),
              "--listings", snapshot(tmp, "l11.json", big_rows, total=700, board="pickup"), "--board", "pickup")
    elapsed = time.time() - start
    check(res.returncode == 0 and elapsed < 20, f"200 x 700 reconciles in {elapsed:.1f}s")
    check("extras: 500 snapshot rows are not in the expected list" in res.stdout,
          "snapshot rows outside the plan are summarised, not dumped")
    res = rec("--expected", queue_file(tmp, "q11.json", big_want),
              "--listings", snapshot(tmp, "l11.json", big_rows, total=700, board="pickup"), "--board", "pickup",
              "--show-extras")
    check(res.stdout.count("\n  - Bulk") >= 1, "--show-extras expands the extras detail")

    # 12. the sheet as an expected source
    wb = Workbook()
    ws = wb.active
    ws.title = "登记"
    for col, value in zip("DEFGH", ["产品名称", "售价", "送券", "供货价", "单件成本"]):
        ws[f"{col}1"] = value
    ws["D2"], ws["E2"], ws["F2"] = "A 500ml", 3.0, 0.9
    ws["D3"], ws["E3"], ws["F3"] = "Not Listed", 8.0, 1.0
    book = tmp / "sheet.xlsx"
    wb.save(book)
    res = rec("--expected", book, "--sheet", "登记", "--listings", s, "--board", "pickup")
    check(res.returncode == 1 and "Not Listed" in res.stdout
          and "the expected source carries no spec names" in res.stdout,
          "a workbook expectation reconciles prices and says the structure dimension was skipped")

    # 13. machine-readable output
    res = rec("--expected", q, "--listings", s, "--board", "pickup", "--out", tmp / "diff.json")
    diff = json.loads((tmp / "diff.json").read_text(encoding="utf-8"))
    check(set(diff) >= {"missing", "duplicates", "price", "structure", "status", "extras"},
          "--out writes the diff with all categories")
    res = rec("--expected", q, "--listings", s, "--board", "pickup", "--out", tmp / "nodir" / "diff.json")
    check(res.returncode == 0 and "WARN cannot write the diff" in res.stderr,
          "an unwritable diff path warns but keeps the verdict's exit code")

    # 14. regressions a cross-review found, each pinned by its own input
    check(module.normalize_spec("500ml*1瓶") != module.normalize_spec("500ml*2瓶"),
          "spec labels keep their count, so a wrong spec row cannot hide behind the name fold")
    res = rec("--expected", queue_file(tmp, "q14a.json", [want(20, "A", 3.0, "500ml*1瓶")]),
              "--listings", snapshot(tmp, "l14a.json", [row("A", 3.0, [{"name": "500ml*2瓶", "price": 3.0}])],
                                     board="pickup"), "--board", "pickup")
    check("STRUCTURE - price matches but the spec rows do not: 1" in res.stdout,
          "same price but 2-bottle rows where the plan says 1 bottle is a structure difference")
    check(norm("洗发水*200ml") != norm("洗发水*400ml"),
          "a trailing size like *400ml is not pack notation, so two shampoos do not collapse into one")
    res = rec("--expected", queue_file(tmp, "q14c.json", [want(21, "A", 9.9, "500ml*1瓶")]),
              "--listings", snapshot(tmp, "l14c.json", [row("A", 3.0, [{"name": "500ml*1瓶", "price": 3.0}],
                                                             status="待审核")], board="pickup"), "--board", "pickup")
    check("PRICE - name matches but the number does not: 1" in res.stdout
          and "STATUS - found, but not live on the shelf: 1" in res.stdout,
          "a price mismatch does not swallow the fact that the row is still awaiting review")
    res = rec("--expected", queue_file(tmp, "q14d.json", [want(22, "G", 3.0, "500ml*1瓶")]),
              "--listings", snapshot(tmp, "l14d.json", [row("G", 3.0, [{"name": "赠品", "price": 0},
                                                                      {"name": "500ml*1瓶", "price": 3.0}])],
                                     board="pickup"), "--board", "pickup")
    check(res.returncode == 0, "a zero-price gift row does not drag the real unit price into a mismatch")
    res = rec("--expected", queue_file(tmp, "q14e.json", [{"n": 23, "name": "C", "price1": 3.0, "v1": "s", "coupon": 1.25}]),
              "--listings", snapshot(tmp, "l14e.json", [row("C", 3.0, [{"name": "s", "price": 3.0, "coupon": 1.2}])],
                                     board="pickup"), "--board", "pickup")
    check("coupon 期望 1.25 实际 1.20" in res.stdout, "a coupon change that never reached the shop is named")
    res = rec("--expected", queue_file(tmp, "q14f.json", [want(24, "D", 3.0, "s")]),
              "--listings", snapshot(tmp, "l14f.json", [row("D", 3.0, [{"name": "s", "price": 3.0}], ident="1"),
                                                        row("D", 3.004, [{"name": "s", "price": 3.004}], ident="2")],
                                     board="pickup"), "--board", "pickup")
    check("DUPLICATE - same name and price listed more than once: 1" in res.stdout,
          "two rows within the price tolerance are duplicates, not two products")
    check("captured:" in rec("--expected", q, "--listings", s, "--board", "pickup").stdout,
          "the header states when the snapshot was taken")


def vprofile(mode="fill-and-submit"):
    tray = lambda key, label, crop, bind, optional=False: {
        "key": key, "label": label, "bind": bind, "crop": crop, "maxCount": 5,
        "uploadTrigger": {"by": "role+name", "value": f"upload-{key}"},
        "thumbLocator": {"by": "css", "value": f".thumb-{key}"},
        "removeLocator": {"by": "css", "value": f".remove-{key}"},
        **({"optional": True} if optional else {}),
        **({"cropConfirm": {"by": "role+name", "value": "裁剪并上传"}} if crop == "square" else {})}
    return {
        "platform": "verify-test",
        "form": {"newUrl": "https://test.invalid/new", "listUrl": "https://test.invalid/list"},
        "image": {"minBytes": 50000, "trays": [tray("main", "商品图", "square", "product"),
                                               tray("detail", "详情图", "any", "product"),
                                               tray("sku", "规格图", "square", "sku", optional=True)]},
        "fields": {
            "name": {"locator": {"by": "placeholder", "value": "请输入商品名称"}, "maxLength": 42},
            "category": {"kind": "cascader", "locator": {"by": "placeholder", "value": "请选择类目"},
                         "expectedSeparator": " / "},
            "scope": {"locator": {"by": "css", "value": ".scope"}, "value": "全域自提点"},
            "spec": {"nameLocator": {"by": "placeholder", "value": "规格名"},
                     "valueLocator": {"by": "placeholder", "value": "规格值"},
                     "skuInputsLocator": {"by": "css", "value": ".sku input"},
                     "skuFieldOrder": ["price", "stock", "points"],
                     "caseSkuLabel": "{per}{unit}*{cu}"}},
        "defaults": {"stock": 10000, "specName": "规格", "rootCategory": "RootCat",
                     "checkedToStart": ["现金+积分", "条幅", "抽佣继承"]},
        "business": {"mode": mode, "pointsMode": "coupon", "minCoupon": 0, "counters": True, "caseSku": True},
    }


def vitem(**over):
    item = {"n": 1, "name": "示例商品500ml", "expectCat": "RootCat / 饮料 / 茶饮料",
            "specName": "规格", "v1": "500ml*1瓶", "v2": "15瓶*箱",
            "price1": 3.0, "price2": 45.0, "pt1": 0.8, "pt2": 12.0, "stock": 10000,
            "sales": 137, "fav": 271}
    item.update(over)
    return item


def vactual(**over):
    actual = {"name": "示例商品500ml", "category": "RootCat / 饮料 / 茶饮料", "scope": "全域自提点",
              "specName": "规格", "specValues": ["500ml*1瓶", "15瓶*箱"],
              "skuRows": [{"price": 3.0, "stock": 10000, "points": 0.8},
                          {"price": 45.0, "stock": 10000, "points": 12.0}],
              "sales": 137, "fav": 271, "checked": 3,
              "images": [{"tray": "main", "bytes": 152341}, {"tray": "detail", "bytes": 201144}],
              "submitted": False}
    actual.update(over)
    return actual


def test_verify(tmp: Path) -> None:
    folder = tmp / "verify"
    folder.mkdir(exist_ok=True)
    tmp = folder

    profile = tmp / "profile.json"
    profile.write_text(json.dumps(vprofile(), ensure_ascii=False), encoding="utf-8")
    queue = tmp / "queue.json"
    queue.write_text(json.dumps([vitem(), vitem(n=2, name="第二件")], ensure_ascii=False), encoding="utf-8")
    good = tmp / "actual.json"
    good.write_text(json.dumps(vactual(), ensure_ascii=False), encoding="utf-8")

    def run_v(*args):
        return subprocess.run([sys.executable, str(SCRIPTS / "verify_item.py"), *[str(a) for a in args]],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    base = [profile, "--actual", good]
    res = run_v("--profile", *base, "--queue", queue, "--n", 1)
    check(res.returncode == 0 and "VERDICT: ok" in res.stdout, "a correct listing passes every rule")
    for rule in ("name", "category", "scope", "spec values", "spec order", "sku rows", "sku price",
                 "sku stock", "sku points", "sales", "fav", "images", "pre-checked", "submit mode"):
        check(f"PASS" in res.stdout and rule in res.stdout, f"the rule {rule!r} is checked and reported")

    def actual_case(slug, **over):
        path = tmp / f"actual-{slug}.json"
        path.write_text(json.dumps(vactual(**over), ensure_ascii=False), encoding="utf-8")
        return run_v("--profile", profile, "--actual", path, "--queue", queue, "--n", 1)

    res = actual_case("cat-off", category="RootCat / 饮料 / 茶饮料 / 绿茶")
    check(res.returncode == 1 and "FAIL category" in res.stdout, "an extra category level is a failure")
    res = actual_case("cat-parent", category="RootCat / 饮料")
    check(res.returncode == 1 and "FAIL category" in res.stdout,
          "a parent node whose label contains the leaf is not accepted as a match")
    res = actual_case("order", specValues=["15瓶*箱", "500ml*1瓶"],
                      skuRows=[{"price": 45.0, "stock": 10000, "points": 12.0},
                               {"price": 3.0, "stock": 10000, "points": 0.8}])
    check("FAIL spec order" in res.stdout and "before the unit row" in res.stdout,
          "a case row sitting before the unit row is blocked - rows can only be appended")
    res = actual_case("rows", skuRows=[{"price": 3.0, "stock": 10000, "points": 0.8}])
    check("FAIL sku rows" in res.stdout and "1 rows for 2 spec values" in res.stdout,
          "fewer SKU rows than spec values is blocked")
    res = actual_case("price", skuRows=[{"price": 3.1, "stock": 10000, "points": 0.8},
                                        {"price": 45.0, "stock": 10000, "points": 12.0}])
    check("FAIL sku price" in res.stdout and "expected 3.0 got 3.1" in res.stdout,
          "a price one tenth off is named with both numbers")
    res = actual_case("stock", skuRows=[{"price": 3.0, "stock": 500, "points": 0.8},
                                        {"price": 45.0, "stock": 10000, "points": 12.0}])
    check("FAIL sku stock" in res.stdout and "expected 10000 got 500" in res.stdout, "stock drift is blocked")

    array_rows = tmp / "actual-array.json"
    array_rows.write_text(json.dumps(vactual(skuRows=[[3.0, 10000, 0.8], [45.0, 10000, 12.0]]),
                                     ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", profile, "--actual", array_rows, "--queue", queue, "--n", 1)
    check(res.returncode == 0, "rows dumped as arrays are keyed by the profile's skuFieldOrder and still pass")
    no_order = json.loads(profile.read_text(encoding="utf-8"))
    no_order["fields"]["spec"].pop("skuFieldOrder")
    no_order_path = tmp / "profile-noorder.json"
    no_order_path.write_text(json.dumps(no_order, ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", no_order_path, "--actual", array_rows, "--queue", queue, "--n", 1)
    check(res.returncode == 2 and "skuFieldOrder" in res.stderr,
          "array rows without a declared field order are refused, not guessed")

    no_points = tmp / "queue-nopoints.json"
    no_points.write_text(json.dumps([vitem(pt1=None, pt2=None)], ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", profile, "--actual", good, "--queue", no_points, "--n", 1)
    check(res.returncode == 0 and "SKIP sku points" in res.stdout,
          "a queue item without points skips that dimension instead of failing it")

    res = actual_case("white", images=[{"tray": "main", "bytes": 8123}, {"tray": "detail", "bytes": 201144}])
    check("FAIL images" in res.stdout and "white crop" in res.stdout and "main" in res.stdout,
          "a stored image under the byte floor is named as a white crop, per tray")
    res = actual_case("notray", images=[{"tray": "main", "bytes": 152341}])
    check("FAIL images" in res.stdout and "detail" in res.stdout,
          "a required upload slot with no read-back entry is blocked")
    res = actual_case("local-size", images=[{"tray": "main", "bytes": None}, {"tray": "detail", "bytes": 2}])
    check("FAIL images" in res.stdout and "stored byte size" in res.stdout,
          "no stored byte size is a failure; the local file size is not evidence")

    fill_only = tmp / "profile-fillonly.json"
    fill_only.write_text(json.dumps(vprofile(mode="fill-only"), ensure_ascii=False), encoding="utf-8")
    submitted = tmp / "actual-submitted.json"
    submitted.write_text(json.dumps(vactual(submitted=True), ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", fill_only, "--actual", submitted, "--queue", queue, "--n", 1)
    check(res.returncode == 1 and "OUT-OF-MODE" in res.stdout,
          "submitting while the agreed mode is fill-only is blocked and labelled out-of-mode")

    res = actual_case("checked", checked=2)
    check("FAIL pre-checked" in res.stdout and "3" in res.stdout,
          "the pre-checked control count is asserted against what the probe recorded")
    res = actual_case("name", name="示例商品500ml ")
    check(res.returncode == 0, "surrounding whitespace in the name is not treated as a difference")
    long_name = "名" * 43
    long_queue = tmp / "queue-longname.json"
    long_queue.write_text(json.dumps([vitem(name=long_name)], ensure_ascii=False), encoding="utf-8")
    long_actual = tmp / "actual-longname.json"
    long_actual.write_text(json.dumps(vactual(name=long_name), ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", profile, "--actual", long_actual, "--queue", long_queue, "--n", 1)
    check("FAIL name" in res.stdout and "at most 42" in res.stdout,
          "a queue name that cannot fit the form is blocked before anyone types it")

    res = run_v("--profile", profile, "--actual", good, "--queue", queue, "--n", 2)
    check(res.returncode == 1 and "FAIL name" in res.stdout,
          "reading back the wrong product's page is a name failure, not a pass")

    missing_actual = tmp / "actual-partial.json"
    missing_actual.write_text(json.dumps({"name": "示例商品500ml"}, ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", profile, "--actual", missing_actual, "--queue", queue, "--n", 1)
    check(res.returncode == 1 and "FAIL category" in res.stdout and "FAIL sku rows" in res.stdout,
          "a read-back that omits required fields fails them instead of skipping silently")

    res = run_v("--profile", profile, "--actual", good)
    check(res.returncode == 2 and "--queue" in res.stderr, "neither --item nor --queue is refused with exit 2")
    res = run_v("--profile", profile, "--actual", good, "--queue", queue, "--n", 99)
    check(res.returncode == 2 and "no item with n=99" in res.stderr, "an unknown queue number is refused")
    res = run_v("--profile", tmp / "missing.json", "--actual", good, "--queue", queue, "--n", 1)
    check(res.returncode == 2, "a missing file is refused with exit 2")
    bad_profile = tmp / "profile-broken.json"
    bad_profile.write_text(json.dumps({"platform": "x"}, ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", bad_profile, "--actual", good, "--queue", queue, "--n", 1)
    check(res.returncode == 2 and "check_profile" in res.stderr, "a non-profile is refused and pointed at the validator")
    res = run_v("--profile", profile, "--actual", queue, "--queue", queue, "--n", 1)
    check(res.returncode == 2 and "expected an object" in res.stderr, "a read-back that is a list is refused")

    single = tmp / "item.json"
    single.write_text(json.dumps(vitem(), ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", profile, "--actual", good, "--item", single)
    check(res.returncode == 0, "--item takes a single extracted queue entry")

    again = run_v("--profile", profile, "--actual", good, "--queue", queue, "--n", 1)
    check(res.stdout == again.stdout, "the same inputs produce the same verdict, byte for byte")

    res = run_v("--profile", profile, "--actual", good, "--queue", queue, "--n", 1, "--out", tmp / "verdict.json")
    verdict = json.loads((tmp / "verdict.json").read_text(encoding="utf-8"))
    check(res.returncode == 0 and verdict["verdict"] == "ok" and len(verdict["checks"]) >= 14,
          "--out writes the per-rule verdict")
    res = run_v("--profile", profile, "--actual", good, "--queue", queue, "--n", 1, "--out", tmp / "no" / "v.json")
    check(res.returncode == 0 and "WARN cannot write the verdict" in res.stderr,
          "an unwritable verdict path warns instead of masking the verdict")
    blocked_actual = tmp / "actual-blocked-out.json"
    blocked_actual.write_text(json.dumps(vactual(skuRows=[{"price": 9.9, "stock": 1, "points": 1},
                                                           {"price": 1, "stock": 1, "points": 1}]),
                                         ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", profile, "--actual", blocked_actual, "--queue", queue, "--n", 1,
                "--out", tmp / "no" / "v.json")
    check(res.returncode == 1 and "VERDICT: blocked" in res.stdout,
          "a blocked verdict survives a failed archive write")

    # 15. regressions from the cross-review: the gate must not bless what it never compared
    extra = tmp / "actual-extra-specs.json"
    extra.write_text(json.dumps(vactual(specValues=["500ml*1瓶", "15瓶*箱", "999ml*2瓶"],
                                         skuRows=[{"price": 3.0, "stock": 10000, "points": 0.8},
                                                  {"price": 45.0, "stock": 10000, "points": 12.0},
                                                  {"price": 18.0, "stock": 10000, "points": 6}]),
                                 ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", profile, "--actual", extra, "--queue", queue, "--n", 1)
    check(res.returncode == 1 and "does not declare" in res.stdout,
          "a third spec value the queue never declared is blocked, not silently unchecked")
    res = run_v("--profile", profile, "--actual", extra, "--queue", queue, "--n", 1, "--allow-extra-specs")
    check(res.returncode == 0 and "rows [3] have no values in the queue item" in res.stdout,
          "opting in for real variants still says out loud which rows went unchecked")
    declared_queue = tmp / "queue-variants.json"
    declared_queue.write_text(json.dumps([vitem(specValues=["500ml*1瓶", "250ml*2瓶", "750ml*3瓶"])],
                                          ensure_ascii=False), encoding="utf-8")
    variants_actual = tmp / "actual-variants.json"
    variants_actual.write_text(json.dumps(vactual(specValues=["500ml*1瓶", "250ml*2瓶"],
                                                   skuRows=[{"price": 3.0, "stock": 10000, "points": 0.8},
                                                            {"price": 6.0, "stock": 10000, "points": 1.6}]),
                                          ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", profile, "--actual", variants_actual, "--queue", declared_queue, "--n", 1)
    check(res.returncode == 1 and "not on the form" in res.stdout and "750ml*3瓶" in res.stdout,
          "a variant the plan declares but the form lacks is blocked")
    variants_actual2 = tmp / "actual-variants-ok.json"
    variants_actual2.write_text(json.dumps(vactual(specValues=["500ml*1瓶", "250ml*2瓶", "750ml*3瓶"],
                                                    skuRows=[{"price": 3.0, "stock": 10000, "points": 0.8},
                                                             {"price": 6.0, "stock": 10000, "points": 1.6},
                                                             {"price": 9.0, "stock": 10000, "points": 2.4}]),
                                           ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", profile, "--actual", variants_actual2, "--queue", declared_queue, "--n", 1)
    check(res.returncode == 0 and "rows [1]" not in res.stdout,
          "declared variants match exactly, so nothing is left unchecked")

    breadcrumb = tmp / "actual-breadcrumb.json"
    breadcrumb.write_text(json.dumps(vactual(category="RootCat > 饮料 > 茶饮料"), ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", profile, "--actual", breadcrumb, "--queue", queue, "--n", 1)
    check(res.returncode == 0, "the page's own path separator is folded; the match is segment by segment")

    no_floor = json.loads(profile.read_text(encoding="utf-8"))
    no_floor["image"]["minBytes"] = 0
    no_floor_path = tmp / "profile-nofloor.json"
    no_floor_path.write_text(json.dumps(no_floor, ensure_ascii=False), encoding="utf-8")
    small_images = tmp / "actual-small.json"
    small_images.write_text(json.dumps(vactual(images=[{"tray": "main", "bytes": 900},
                                                        {"tray": "detail", "bytes": 900}]),
                                       ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", no_floor_path, "--actual", small_images, "--queue", queue, "--n", 1)
    check(res.returncode == 0, "an explicit minBytes of 0 means no floor, not the default 50 KB")

    # 16. the sandbox's own trap: a column the page requires that the plan says nothing about
    weight_profile = json.loads(profile.read_text(encoding="utf-8"))
    weight_profile["fields"]["spec"]["skuFieldOrder"] = ["price", "stock", "points", "weight"]
    weight_profile_path = tmp / "profile-weightcol.json"
    weight_profile_path.write_text(json.dumps(weight_profile, ensure_ascii=False), encoding="utf-8")
    no_weight = tmp / "actual-no-weight.json"
    no_weight.write_text(json.dumps(vactual(), ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", weight_profile_path, "--actual", no_weight, "--queue", queue, "--n", 1)
    check(res.returncode == 1 and "FAIL sku numbers filled" in res.stdout and "weight" in res.stdout,
          "a declared column left empty blocks the submit even when the plan carries no value for it")
    with_weight = tmp / "actual-weight.json"
    with_weight.write_text(json.dumps(vactual(skuRows=[{"price": 3.0, "stock": 10000, "points": 0.8, "weight": 560},
                                                       {"price": 45.0, "stock": 10000, "points": 12.0,
                                                        "weight": 8400}]),
                                      ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", weight_profile_path, "--actual", with_weight, "--queue", queue, "--n", 1)
    check(res.returncode == 0 and "PASS sku numbers filled" in res.stdout,
          "filling every declared column clears the gate, which is what the real form demanded")
    toggle_profile = json.loads(profile.read_text(encoding="utf-8"))
    toggle_profile["fields"]["spec"]["skuFieldOrder"] = ["price", "stock", "points", "listed"]
    toggle_path = tmp / "profile-toggle.json"
    toggle_path.write_text(json.dumps(toggle_profile, ensure_ascii=False), encoding="utf-8")
    toggle_actual = tmp / "actual-toggle.json"
    toggle_actual.write_text(json.dumps(vactual(skuRows=[{"price": 3.0, "stock": 10000, "points": 0.8, "listed": True},
                                                          {"price": 45.0, "stock": 10000, "points": 12.0,
                                                           "listed": True}]),
                                        ensure_ascii=False), encoding="utf-8")
    res = run_v("--profile", toggle_path, "--actual", toggle_actual, "--queue", queue, "--n", 1)
    check(res.returncode == 0, "a toggle column is not demanded as a number")

    gbk_actual = tmp / "actual-gbk.json"
    gbk_actual.write_bytes(json.dumps(vactual(), ensure_ascii=False).encode("gbk"))
    res = run_v("--profile", profile, "--actual", gbk_actual, "--queue", queue, "--n", 1)
    check(res.returncode == 2 and "not UTF-8" in res.stderr,
          "a non-UTF-8 read-back is an input error (2), never a verdict (1) an agent would re-try")
    res = run_v("--profile", profile, "--actual", tmp / "actual-price.json", "--queue", queue, "--n", 1, "--quiet")
    check("PASS" not in res.stdout and "FAIL sku price" in res.stdout, "--quiet hides the passing rules")

    source = (SCRIPTS / "verify_item.py").read_text(encoding="utf-8")
    check(not any(tok in source for tok in ("urllib", "socket", "requests", "http.client")),
          "verify_item compares files only - no network module in the source")
    example = json.loads((ROOT / "skills" / "auto-list-products" / "actual.example.json").read_text(encoding="utf-8"))
    check({"name", "category", "specValues", "skuRows", "images", "submitted"} <= set(example),
          "actual.example.json documents every field the script reads")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        profile = test_profile(tmp)
        test_crop(tmp)
        test_images(tmp)
        test_queue(tmp, profile)
        test_xlsx(tmp)
        test_reconcile(tmp)
        test_verify(tmp)
    if failures:
        print(f"\n{len(failures)} smoke failures:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nsmoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
