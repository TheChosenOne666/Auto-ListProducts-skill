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


def test_boards(tmp: Path) -> None:
    folder = tmp / "boards"
    folder.mkdir(exist_ok=True)
    tmp = folder

    base = vprofile(mode="fill-and-submit")
    base["probedAt"] = "2026-11-04T22:40:00+08:00"
    base["fields"]["submit"] = {"button": {"by": "role+name", "value": "提交审核"},
                                "confirmButton": {"by": "role+name", "value": "确定"},
                                "successWhen": "route-changed", "successDetail": "#/new"}
    base["fields"]["optional"] = {"points": {"locator": {"by": "css", "value": "[data-field=points]"},
                                             "decimals": 1}}
    base["business"]["pointsMode"] = "coupon"
    base["defaults"]["rootCategory"] = "自提根"
    base["boards"] = {
        "pickup": {"label": "到店自提", "form": {"newUrl": "https://t/#/p", "listUrl": "https://t/#/pl"}},
        "express": {"label": "快递发货",
                    "form": {"newUrl": "https://t/#/e", "listUrl": "https://t/#/el"},
                    "defaults": {"rootCategory": "快递根", "checkedToStart": []},
                    "fields": {"without": ["scope"],
                               "spec": {"skuFieldOrder": ["price", "stock", "points", "weight", "volume"]}},
                    "business": {"couponOffset": -1}}}
    prof = tmp / "profile-boards.json"
    prof.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")

    products = queue_file(tmp, "products-boards.json",
                          [{"n": 1, "name": "茶 A", "size": "500ml", "unit": "瓶", "per": 15, "cu": "箱",
                            "supply": 10.5, "retail": 3.0, "cat": "TEA", "img": "a.jpg"}])
    cats = tmp / "cats-boards.json"
    cats.write_text(json.dumps({"TEA": {"l2": "饮料", "l3": "茶"}}, ensure_ascii=False), encoding="utf-8")

    res = run("make_queue.py", "--profile", prof, "--products", products, "--categories", cats,
              "--out", tmp / "qb-p.json", "--board", "pickup")
    check(res.returncode == 0, "board pickup builds")
    item_p = json.loads((tmp / "qb-p.json").read_text(encoding="utf-8"))[0]
    res = run("make_queue.py", "--profile", prof, "--products", products, "--categories", cats,
              "--out", tmp / "qb-e.json", "--board", "express")
    item_e = json.loads((tmp / "qb-e.json").read_text(encoding="utf-8"))[0]
    check(item_p["expectCat"].startswith("自提根 / ") and item_e["expectCat"].startswith("快递根 / "),
          "each section uses its own category root")
    check(item_p["price1"] == item_e["price1"] == 3.0, "the retail price never moves with the board")
    check(item_p["coupon"] == 2.3 and item_e["coupon"] == 1.3, "the coupon carries the section offset")
    check(item_e["pt1"] == 1.3, "points derived from the coupon follow the offset")
    check(item_p["board"] == "pickup" and item_e["board"] == "express", "items are tagged for the audit")

    crushed = json.loads(json.dumps(base))
    crushed["boards"]["express"]["business"]["couponOffset"] = -9
    crushed_path = tmp / "profile-crushed.json"
    crushed_path.write_text(json.dumps(crushed, ensure_ascii=False), encoding="utf-8")
    res = run("make_queue.py", "--profile", crushed_path, "--products", products, "--categories", cats,
              "--out", tmp / "qb-crushed.json", "--board", "express")
    check(res.returncode == 1 and "offset -9" in res.stdout and not (tmp / "qb-crushed.json").exists(),
          "an offset that crushes the coupon aborts, names the product and writes no queue")

    res = run("make_queue.py", "--profile", prof, "--products", products, "--categories", cats,
              "--out", tmp / "qb-bad.json", "--board", "courier")
    check(res.returncode == 2 and "defines: ['express', 'pickup']" in res.stderr,
          "an unknown board is an input error that lists the real ones")
    flat = json.loads(json.dumps(base))
    flat.pop("boards")
    flat_path = tmp / "profile-flat.json"
    flat_path.write_text(json.dumps(flat, ensure_ascii=False), encoding="utf-8")
    res = run("make_queue.py", "--profile", flat_path, "--products", products, "--categories", cats,
              "--out", tmp / "qb-flat.json", "--board", "pickup")
    check(res.returncode == 2 and "defines: none" in res.stderr,
          "asking for a board on a single-section profile is refused, not ignored")
    res = run("make_queue.py", "--profile", flat_path, "--products", products, "--categories", cats,
              "--out", tmp / "qb-flat2.json")
    check(res.returncode == 0, "the same profile still works with no board at all (backward compatible)")

    res = run("check_profile.py", prof)
    check(res.returncode == 0 and "boards        : 2 section(s)" in res.stdout
          and "[express]" in res.stdout and "absent on this section" in res.stdout
          and "coupon     : -1 versus the base coupon" in res.stdout,
          "the validator summarises each section instead of blurring them together")
    broken = json.loads(json.dumps(base))
    broken["boards"]["express"]["fields"]["unknownThing"] = {}
    broken["boards"]["express"]["business"]["couponOffset"] = "five"
    broken["boards"]["express"].pop("label")
    broken_path = tmp / "profile-broken-boards.json"
    broken_path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    res = run("check_profile.py", broken_path, "--quiet")
    check(res.returncode == 1 and "label missing" in res.stdout and "unknown override 'unknownThing'" in res.stdout
          and "couponOffset must be a number" in res.stdout,
          "a bad board block is refused with the offending keys named")

    queue_b = queue_file(tmp, "queue-boards.json",
                         [{"n": 1, "name": "茶 A", "price1": 3.0, "price2": 45.0,
                           "v1": "500ml*1瓶", "v2": "15瓶*箱", "board": "express"}])
    express_actual = tmp / "actual-express.json"
    express_actual.write_text(json.dumps({
        "name": "茶 A", "category": "快递根 / 饮料 / 茶", "specName": "规格",
        "specValues": ["500ml*1瓶", "15瓶*箱"],
        "skuRows": [{"price": 3.0, "stock": 10000, "points": 1.3, "weight": 560, "volume": 0.01},
                    {"price": 45.0, "stock": 10000, "points": 19.5, "weight": 8400, "volume": 0.2}],
        "checked": 0, "images": [{"tray": "main", "bytes": 168000}, {"tray": "detail", "bytes": 201000}],
        "submitted": False},
        ensure_ascii=False), encoding="utf-8")
    res = subprocess.run([sys.executable, str(SCRIPTS / "verify_item.py"), "--profile", prof, "--board", "express",
                          "--actual", express_actual, "--queue", queue_b, "--n", "1"],
                         capture_output=True, text=True, encoding="utf-8")
    check(res.returncode == 0 and "SKIP scope" in res.stdout
          and "section 快递发货 has no scope field on its form" in res.stdout,
          "the gate skips the field this section does not have, and says which section")
    thin = json.loads(express_actual.read_text(encoding="utf-8"))
    for row in thin["skuRows"]:
        row.pop("weight")
    thin_path = tmp / "actual-express-thin.json"
    thin_path.write_text(json.dumps(thin, ensure_ascii=False), encoding="utf-8")
    res = subprocess.run([sys.executable, str(SCRIPTS / "verify_item.py"), "--profile", prof, "--board", "express",
                          "--actual", thin_path, "--queue", queue_b, "--n", "1"],
                         capture_output=True, text=True, encoding="utf-8")
    check(res.returncode == 1 and "FAIL sku numbers filled" in res.stdout and "weight" in res.stdout,
          "the express ruler demands the weight the express form demands")
    res = subprocess.run([sys.executable, str(SCRIPTS / "verify_item.py"), "--profile", prof, "--board", "pickup",
                          "--actual", express_actual, "--queue", queue_b, "--n", "1"],
                         capture_output=True, text=True, encoding="utf-8")
    check(res.returncode == 1 and "FAIL scope" in res.stdout,
          "measuring the express form with the pickup ruler is caught the other way round too")
    res = subprocess.run([sys.executable, str(SCRIPTS / "verify_item.py"), "--profile", prof, "--board", "nope",
                          "--actual", express_actual, "--queue", queue_b, "--n", "1"],
                         capture_output=True, text=True, encoding="utf-8")
    check(res.returncode == 2 and "unknown --board" in res.stderr, "the gate refuses an unknown board")


def test_prepare_images(tmp: Path) -> None:
    folder = tmp / "assets"
    src = folder / "src"
    src.mkdir(parents=True, exist_ok=True)

    def pattern(w: int, h: int) -> Image.Image:
        im = Image.new("RGB", (w, h))
        px = im.load()
        for y in range(h):
            for x in range(w):
                px[x, y] = ((x * 3 + y * 5) % 251, (x * 7 + y * 11) % 249, (x * 13 + y * 17) % 243)
        return im

    def save(name: str, w: int, h: int, blank=False) -> str:
        im = Image.new("RGB", (w, h), "white") if blank else pattern(w, h)
        im.save(src / name)
        return name

    variants = [save("a-1.jpg", 800, 800), save("a-2.jpg", 800, 800), save("a-3.jpg", 800, 800)]
    scenes = [save("scene-1.jpg", 1200, 900), save("scene-2.jpg", 1200, 900)]
    info = save("a-info.jpg", 768, 1024)
    small = save("small.jpg", 300, 300)
    save("tiny.jpg", 400, 400)
    white = save("white.jpg", 800, 800, blank=True)

    prof = tmp / "profile.json"
    prof.write_text(json.dumps(vprofile(), ensure_ascii=False), encoding="utf-8")

    def plan(*items) -> Path:
        path = tmp / "plan.json"
        body = [i for i in items if i]
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return path

    def run_prep(dst: str, products_path: Path, *extra, prof_path=None, edge="640"):
        cmd = [sys.executable, str(SCRIPTS / "prepare_images.py"), "--profile", prof_path or prof,
               "--products", products_path, "--src", src, "--dst", folder / dst,
               "--edge", str(edge), *extra]
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    def manifest_of(dst: str, products_path: Path, *extra, prof_path=None) -> tuple[int, str, list]:
        res = run_prep(dst, products_path, "--manifest", folder / f"{dst}-manifest.json", *extra,
                       prof_path=prof_path)
        path = folder / f"{dst}-manifest.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        # stderr is folded in: refusals are written there, and the checks below care about both
        return res.returncode, (res.stdout or "") + (res.stderr or ""), data

    # 1. variants then scenes fill the main slots; detail keeps its own order and is never cropped
    full = plan({"n": 1, "name": "多色商品",
                 "variants": [{"label": f"色{i}", "src": v} for i, v in enumerate(variants)],
                 "scenes": scenes, "infographic": info})
    rc, out, man = manifest_of("m1", full)
    entry = man[0]
    check(rc == 0 and len(entry["main"]) == 5, "five main slots filled from three variants plus two scenes")
    check(all(row["w"] == row["h"] for row in entry["main"]), "every main slot came out square")
    check(all(row["action"].startswith("center-crop") for row in entry["main"]),
          "anything larger than the target edge is centre-cropped, never padded")
    check(entry["main"][3]["src"] == scenes[0], "scenes fill the slots the variants leave over")
    check(entry["main"][0]["src"] == variants[0], "variants lead the main slots")
    check(entry["detail"][0]["src"] == info and "infographic first" in entry["detail_reason"],
          "the named infographic leads the detail slots, and the reason is recorded")
    check(all(row["action"] == "keep" for row in entry["detail"]),
          "the any-ratio tray uploads as-is, nothing cropped")
    check(entry["detail"][0]["w"] == 768 and entry["detail"][0]["h"] == 1024,
          "a 3:4 infographic keeps 3:4 in the detail tray")
    check(len(entry["detail"]) == 5, "the detail tray is truncated to its slot count")
    check(set(entry["skuImages"]) == {"色0", "色1", "色2"}, "the per-SKU slot binds one image per variant label")
    check(not entry["short"] and not entry["suspect"], "a complete plan reports no shortfall")

    # 2. the centre crop really takes the middle of the source, not a padded frame.
    # a smooth gradient is used here on purpose: on a high-frequency pattern JPEG noise alone is ~50,
    # which would make any tight threshold meaningless
    gradient = Image.new("RGB", (1200, 900))
    gpx = gradient.load()
    for gy in range(900):
        for gx in range(1200):
            gpx[gx, gy] = (gx % 256, gy % 256, (gx + gy) % 256)
    gradient.save(src / "grad.jpg", quality=95)
    crop_plan = plan({"n": 20, "name": "裁切检查", "scenes": ["grad.jpg"]})
    rc, out, man = manifest_of("m20", crop_plan, "--edge", "900")
    produced = man[0]["main"][0]
    check(produced["action"] == "center-crop" and produced["w"] == 900 and produced["h"] == 900,
          "a 1200x900 scene becomes a 900x900 centre crop at edge 900")
    with Image.open(src / "grad.jpg") as raw:
        expected = raw.convert("RGB").crop((150, 0, 1050, 900))
    with Image.open(folder / "m20" / produced["file"]) as made:
        made = made.convert("RGB")
        corners = [(0, 0), (made.width - 1, 0), (0, made.height - 1), (made.width - 1, made.height - 1)]
        close = all(sum(abs(a - b) for a, b in zip(made.getpixel(xy), expected.getpixel(xy))) < 30
                    for xy in corners)
        flat = len({made.getpixel(xy) for xy in corners}) == 1
        all_white = all(min(made.getpixel(xy)) > 250 for xy in corners)
        check(made.size == expected.size and close and not flat and not all_white,
              "the output is the centre of the source, not a padded frame with fill-colour corners")

    # 3. never upscale
    small_plan = plan({"n": 2, "name": "小图商品", "variants": [{"label": "小", "src": small}]})
    rc, out, man = manifest_of("m2", small_plan)
    check(man[0]["main"][0]["w"] == 300 and man[0]["main"][0].get("note") == "not-upscaled",
          "a 300px source stays 300px instead of being blown up to the target edge")

    # 4. a short set is reported, never filled with repeats
    short_plan = plan({"n": 3, "name": "缺图商品",
                       "variants": [{"label": "红", "src": variants[0]}, {"label": "蓝", "src": variants[1]}],
                       "scenes": [scenes[0]]})
    rc, out, man = manifest_of("m3", short_plan)
    check(rc == 1 and man[0]["short"] and man[0]["short"][0]["missing"] == 2,
          "two variants plus one scene against five slots reports the shortfall instead of inventing")
    check("not filling with repeats" in out, "the report says it refused to repeat an image")

    # 5. the same file twice in one slot group is an error
    dup_plan = plan({"n": 4, "name": "重复图", "variants": [{"label": "红", "src": variants[0]},
                                                          {"label": "红2", "src": variants[0]}]})
    rc, out, man = manifest_of("m4", dup_plan)
    check(rc == 2 and "used twice" in out, "using one file twice in a slot group is refused")

    # 6. a missing source file is refused, not skipped
    missing_plan = plan({"n": 5, "name": "缺文件", "variants": [{"label": "红", "src": "nope.jpg"}]})
    rc, out, man = manifest_of("m5", missing_plan)
    check(rc == 2 and "missing source file nope.jpg" in out, "a missing source names the file and exits 2")

    # 7. a blank asset is flagged through the byte and colour floor
    white_plan = plan({"n": 6, "name": "白图商品", "variants": [{"label": "白", "src": white}]})
    rc, out, man = manifest_of("m6", white_plan)
    check(rc == 1 and "SUSPECT" in out and man[0]["suspect"], "a white asset is marked suspect, not shipped silently")

    # 8. an explicit order from the plan wins over the rule
    explicit_plan = plan({"n": 7, "name": "手工排位",
                          "variants": [{"label": "红", "src": variants[0]}], "scenes": scenes,
                          "trays": {"main": [scenes[1], variants[0], scenes[0]], "detail": None}})
    rc, out, man = manifest_of("m7", explicit_plan)
    check([r["src"] for r in man[0]["main"]] == [scenes[1], variants[0], scenes[0]]
          and "explicit order" in man[0]["main_reason"],
          "when the user says which images go where, that order is used and recorded as the reason")

    # 9. a single-image product gets one image and nothing else
    single_plan = plan({"n": 8, "name": "单图", "single": "tiny.jpg"})
    rc, out, man = manifest_of("m8", single_plan)
    check(len(man[0]["main"]) == 1 and "single-image product" in man[0]["main_reason"],
          "one image, nothing added, and the reason says so")

    # 10. idempotent, and the example plan documents the fields
    _, _, again = manifest_of("m8b", single_plan)
    check(man[0]["main"] == again[0]["main"], "the same plan produces the same manifest")
    example = json.loads((ROOT / "skills" / "auto-list-products" / "products-images.example.json").read_text(encoding="utf-8"))
    check({"variants", "scenes", "infographic"} <= set(example[0]) and "single" in example[1]
          and "trays" in example[2], "products-images.example.json documents every field the script reads")
    source = (SCRIPTS / "prepare_images.py").read_text(encoding="utf-8")
    check(not any(tok in source for tok in ("urllib", "socket", "requests", "http.client")),
          "prepare_images works on local files only - no network module in the source")


    # 11. regressions a cross-review found: the silent-successes family
    def tray(key, crop="square", maxc=3, bind="product"):
        return {"key": key, "label": key, "bind": bind, "crop": crop, "maxCount": maxc,
                "uploadTrigger": {"by": "css", "value": "#" + key},
                "thumbLocator": {"by": "css", "value": ".t"}, "removeLocator": {"by": "css", "value": ".r"}}

    def profile_with(trays) -> Path:
        path = tmp / "profile-trays.json"
        # minBytes is set low here on purpose: these cases are about slot shape, not about the white-image floor
        path.write_text(json.dumps({"platform": "t", "image": {"minBytes": 1000, "trays": trays},
                                    "fields": {}, "defaults": {}, "business": {}}, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def run_with(p_path, items, dst, *extra):
        plan_path = plan(*items)
        return manifest_of(dst, plan_path, *extra, prof_path=p_path)

    empty_prof = profile_with([tray("main", maxc=3)])
    rc, out, man = run_with(empty_prof, [{"n": 1, "name": "啥也没有"}], "r1")
    check(rc == 1 and "every slot filled" not in out and man[0]["main"] == []
          and man[0]["short"][0]["missing"] == 3,
          "a product with no candidates is a shortfall, not a silent skip that says every slot is full")
    rc, out, man = run_with(empty_prof, [{"n": 1, "name": "空列表", "trays": {"main": []}}], "r2")
    check(rc == 1 and man[0]["short"], "an explicitly empty slot list is still reported as short")

    named_prof = profile_with([tray("主图", maxc=1)])
    rc, out, man = run_with(named_prof, [{"n": 1, "name": "单图", "single": small}], "r3")
    check(rc == 0 and len(man[0]["主图"]) == 1,
          "a single-image product fills the first product-level slot whatever the platform calls it")

    rc, out, man = run_with(profile_with([tray("main", maxc=3)]),
                            [{"n": 9, "name": "甲", "single": small}, {"n": 9, "name": "乙", "single": info}], "r4")
    check(rc == 2 and "duplicate product numbers" in out and man is None,
          "two products sharing a number are refused - their files would overwrite each other")
    check(not list((folder / "r4").glob("*.jpg")) if (folder / "r4").exists() else True,
          "a refused run writes no images")

    for label, trays in (("maxCount", [{k: v for k, v in tray("main").items() if k != "maxCount"}]),
                         ("reserved-key", [tray("short")]),
                         ("duplicate-key", [tray("main"), tray("main", crop="any")]),
                         ("bad-crop", [tray("main", crop="roundish")])):
        rc, out, man = run_with(profile_with(trays), [{"n": 1, "name": "档案", "single": small}], f"bad-{label}")
        check(rc == 2, f"a tray with a bad definition ({label}) is refused before anything is written")

    rc, out, man = run_with(profile_with([tray("规格图", maxc=5, bind="sku")]),
                            [{"n": 1, "name": "无标签", "variants": [{"src": small}]}], "r5")
    check(rc == 2 and "no label" in out, "a variant without a label is refused, not silently unbound")

    Image.new("RGB", (600, 800), (30, 60, 90)).save(src / "tall.jpg")
    rc, out, man = run_with(profile_with([tray("main", maxc=1)]),
                            [{"n": 1, "name": "非方小图", "scenes": ["tall.jpg"]}], "r6")
    check(man[0]["main"][0]["w"] == 600 and man[0]["main"][0].get("note") == "not-upscaled",
          "any output smaller than the requested edge says it was not upscaled, cropped or not")

    rc, out, man = run_with(profile_with([tray("规格图", maxc=3, bind="sku")]),
                            [{"n": 1, "name": "怪标签", "variants": [{"label": "黑\n蓝", "src": small}]}], "r7")
    bound = list(man[0]["skuImages"].items()) if man else []
    check(rc == 0 and bound and "\n" not in bound[0][1]["file"] and bound[0][0] == "黑\n蓝",
          "a control character is sanitised in the filename while the label stays verbatim for the SKU row")

    rc, out, man = run_with(empty_prof, [{"n": 1, "name": "啥也没有"}], "r8", "--sheet", str(folder / "r8-sheet.jpg"))
    check("contact sheet:" not in out and not (folder / "r8-sheet.jpg").exists(),
          "no contact sheet is announced when there is nothing to put on it")


def probe_dump(**over):
    base = {
        "capturedAt": "2026-11-01T10:00:00+08:00", "board": "pickup",
        "form": {"newUrl": "https://t/#/new", "listUrl": "https://t/#/list"},
        "fields": [
            {"label": "商品名称", "required": True, "kind": "text", "role": "name", "maxLength": 42, "default": "",
             "locator": {"by": "placeholder", "value": "请输入商品名称"}},
            {"label": "上架范围", "required": True, "kind": "select", "role": "scope",
             "options": ["全域自提点", "指定自提点"], "default": "全域自提点"},
            {"label": "重量(g)", "required": False, "kind": "number", "in": "sku-row",
             "emptyBlocksSubmit": True, "default": ""}],
        "categoryTree": {"酒水饮料": {"茶饮料": {"乌龙茶": None}, "汽水": None}, "方便速食": {"泡面": None}},
        "separator": " / ",
        "trays": [{"key": "main", "crop": "square", "maxCount": 1, "bind": "product"},
                  {"key": "detail", "crop": "any", "maxCount": 5, "bind": "product"}],
        "skuColumns": ["价格", "库存", "积分", "重量(g)", "是否上架"],
        "submit": {"button": "提交审核", "confirm": "确认提交审核？", "success": "route leaves #/new"},
        "observations": ["库存预填 10000"]}
    base.update(over)
    return base


def test_diff_probe(tmp: Path) -> None:
    folder = tmp / "probe"
    folder.mkdir(exist_ok=True)
    tmp = folder

    def dump_file(name: str, doc) -> Path:
        path = tmp / name
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def diff(old, new, *extra):
        return subprocess.run([sys.executable, str(SCRIPTS / "diff_probe.py"), str(old), str(new), *extra],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    base_path = dump_file("a.json", probe_dump())
    same_path = dump_file("b-same.json", probe_dump(capturedAt="2026-11-04T10:00:00+08:00"))
    res = diff(base_path, same_path)
    check(res.returncode == 0 and "no drift" in res.stdout, "two identical probes report no drift")
    again = diff(base_path, same_path)
    check(res.stdout == again.stdout, "the same pair produces byte-identical output")

    self_check = dump_file("b-clone.json", probe_dump())
    res = diff(base_path, self_check)
    check(res.returncode == 2 and "one capture compared with itself" in res.stderr,
          "two dumps of the same capturedAt cannot report 'no drift' - that pair proves nothing")

    added = dump_file("b-add.json", probe_dump(
        categoryTree={"酒水饮料": {"茶饮料": {"乌龙茶": None, "红茶": None}, "汽水": None, "果汁": None},
                      "方便速食": {"泡面": None}}))
    res = diff(base_path, added)
    check(res.returncode == 1 and "+ category 酒水饮料 / 茶饮料 / 红茶" in res.stdout
          and "+ category 酒水饮料 / 果汁" in res.stdout, "added nodes are reported with their full path")

    removed = dump_file("b-del.json", probe_dump(
        categoryTree={"酒水饮料": {"茶饮料": {"乌龙茶": None}}, "方便速食": {"泡面": None}}))
    res = diff(base_path, removed)
    check(res.returncode == 1 and "- category 酒水饮料 / 汽水" in res.stdout
          and "category assertion" in res.stdout, "a removed node says which queue items will now fail")

    renamed = dump_file("b-ren.json", probe_dump(
        categoryTree={"酒水饮料": {"茶饮料": {"乌龙茶": None}, "汽水": None}, "方便速食": {"速食面": None}}))
    res = diff(base_path, renamed)
    check("- category 方便速食 / 泡面" in res.stdout and "+ category 方便速食 / 速食面" in res.stdout,
          "a renamed leaf shows as the removal and the addition it actually is")

    fields = probe_dump(fields=[
        {"label": "商品名称", "required": True, "kind": "text", "role": "name", "maxLength": 60, "default": "",
         "locator": {"by": "id", "value": "f-name"}},
        {"label": "上架范围", "required": False, "kind": "select", "role": "scope",
         "options": ["全域自提点", "指定自提点", "全部门店"], "default": "指定自提点"},
        {"label": "重量(g)", "required": True, "kind": "number", "in": "sku-row",
         "emptyBlocksSubmit": False, "default": ""},
        {"label": "备注", "required": False, "kind": "text", "default": ""}])
    fields_path = dump_file("b-fields.json", fields)
    res = diff(base_path, fields_path)
    lines = res.stdout
    check("~ field 商品名称 maxLength 42 -> 60" in lines,
          "a name length change is reported: the queue cuts names at this number")
    check("~ field 商品名称 locator" in lines,
          "a changed attribute that is not in the named list (locator) still surfaces, with the values")
    check("~ field 上架范围 required marker True -> False" in lines, "a required flip is reported")
    check("~ field 上架范围 default" in lines and "指定自提点" in lines, "a default change is reported")
    check("~ field 上架范围 options" in lines and "全部门店" in lines, "an options change is reported")
    check("~ field 重量(g)@sku-row empty-blocks-submit True -> False" in lines,
          "a field that no longer blocks submit is reported - the measured behaviour, not the asterisk")
    check("+ field 备注" in lines, "a new field is reported")

    reordered = dump_file("b-order.json", probe_dump(skuColumns=["价格", "库存", "重量(g)", "积分", "是否上架"]))
    res = diff(base_path, reordered)
    check("sku column order" in res.stdout and "off by one column" in res.stdout,
          "the same columns in a new order are called out as an order change, not as no change")
    grew = dump_file("b-grow.json", probe_dump(skuColumns=["价格", "库存", "积分", "重量(g)", "体积", "是否上架"]))
    res = diff(base_path, grew)
    check("+ sku column 体积" in res.stdout, "a new sku column is reported")

    trays = probe_dump(trays=[{"key": "main", "crop": "square", "maxCount": 5, "bind": "product"},
                              {"key": "detail", "crop": "any", "maxCount": 5, "bind": "product"},
                              {"key": "sku", "crop": "square", "maxCount": 1, "bind": "sku"}])
    trays_path = dump_file("b-trays.json", trays)
    res = diff(base_path, trays_path)
    check("~ tray main maxCount 1 -> 5" in res.stdout and "+ upload tray sku" in res.stdout,
          "slot count changes and new slots are reported")

    submit = dump_file("b-submit.json", probe_dump(submit={"button": "提交", "confirm": "确认提交？",
                                                           "success": "toast 提交成功"}))
    res = diff(base_path, submit)
    check("~ submit button" in res.stdout and "~ submit success" in res.stdout,
          "submit wording changes are reported, because locators key on them")

    obs = dump_file("b-obs.json", probe_dump(observations=["类目变更后需要刷新页面"]))
    res = diff(base_path, obs)
    check("+ observation 类目变更后需要刷新页面" in res.stdout, "new observations surface")
    check("- observation 库存预填 10000" in res.stdout and "the trap stays on the page" in res.stdout,
          "a note that disappeared is reported too: deleting the record does not delete the trap")

    express = dump_file("b-express.json", probe_dump(board="express", capturedAt="2026-11-04T10:00:00+08:00",
                                                     fields=[{"label": "体积", "required": False, "kind": "number",
                                                              "in": "sku-row", "emptyBlocksSubmit": True}]))
    res = diff(base_path, express)
    check(res.returncode == 2 and "each board is compared against its own history" in res.stderr,
          "a pickup probe against an express probe is refused: that is a section difference, not drift")
    res = diff(base_path, express, "--cross-board")
    check(res.returncode == 1 and "--cross-board" in res.stdout and "- field 商品名称" in res.stdout,
          "--cross-board answers the other question and labels itself while doing it")

    good_profile = {
        "platform": "t", "form": {"newUrl": "https://t/#/new", "listUrl": "https://t/#/list"},
        "fields": {"name": {"locator": {"by": "placeholder", "value": "请输入商品名称"}, "maxLength": 42},
                   "category": {"kind": "cascader", "locator": {"by": "placeholder", "value": "请选择商品类目"},
                                "expectedSeparator": " / "},
                   "scope": {"locator": {"by": "css", "value": "#f-scope"}, "value": "全域自提点"},
                   "spec": {"nameLocator": {"by": "placeholder", "value": "规格名"},
                            "valueLocator": {"by": "placeholder", "value": "规格值"},
                            "skuInputsLocator": {"by": "css", "value": ".sku"},
                            "skuFieldOrder": ["price", "stock", "points", "weight", "listed"],
                            "caseSkuLabel": "{per}{unit}*箱"},
                   "submit": {"button": {"by": "role+name", "value": "提交审核"}}},
        "image": {"minBytes": 50000, "trays": [{"key": "main", "label": "主图", "bind": "product", "crop": "square",
                                                "maxCount": 1, "uploadTrigger": {"by": "css", "value": "#m"},
                                                "thumbLocator": {"by": "css", "value": ".t"},
                                                "removeLocator": {"by": "css", "value": ".r"}},
                                               {"key": "detail", "label": "详情图", "bind": "product", "crop": "any",
                                                "maxCount": 5, "uploadTrigger": {"by": "css", "value": "#d"},
                                                "thumbLocator": {"by": "css", "value": ".t"},
                                                "removeLocator": {"by": "css", "value": ".r"}}]},
        "defaults": {"rootCategory": "酒水饮料", "specName": "规格", "stock": 10000, "checkedToStart": []},
        "business": {"mode": "fill-and-submit", "pointsMode": "none", "minCoupon": 0, "counters": False,
                     "caseSku": True}}
    gp = tmp / "profile-good.json"
    gp.write_text(json.dumps(good_profile, ensure_ascii=False, indent=1), encoding="utf-8")
    res = diff(base_path, same_path, "--check-profile", gp)
    check(res.returncode == 0 and "profile matches the probe" in res.stdout,
          "a profile consistent with the probe passes")

    drifted = json.loads(gp.read_text(encoding="utf-8"))
    drifted["defaults"]["rootCategory"] = "生鲜"
    drifted["fields"]["scope"]["value"] = "全域门店"
    drifted["fields"]["spec"]["skuFieldOrder"] = ["price", "stock"]
    drifted["fields"]["category"]["expectedSeparator"] = " > "
    drifted["image"]["trays"][0]["maxCount"] = 9
    drifted["image"]["trays"].append({"key": "video", "label": "视频", "bind": "product", "crop": "none",
                                      "maxCount": 1, "uploadTrigger": {"by": "css", "value": "#v"},
                                      "thumbLocator": {"by": "css", "value": ".t"},
                                      "removeLocator": {"by": "css", "value": ".r"}})
    drifted["fields"]["name"]["maxLength"] = 80
    dp = tmp / "profile-drifted.json"
    dp.write_text(json.dumps(drifted, ensure_ascii=False, indent=1), encoding="utf-8")
    res = diff(base_path, same_path, "--check-profile", dp)
    for expected_line in ("root category '生鲜' is not in", "profile scope '全域门店' is not among",
                          "skuFieldOrder has 2 entries", "expectedSeparator ' > ' != observed",
                          "profile tray 'video' was not observed in", "tray main: profile maxCount 9 but observed 1",
                          "name maxLength 80 but observed 42"):
        check(expected_line in res.stdout, f"profile consistency names: {expected_line}")
    check(res.returncode == 1, "an inconsistent profile exits 1")

    # 审查复现的六类误判，逐条钉住
    misattribution = probe_dump(capturedAt="2026-11-04T12:00:00+08:00", fields=[
        {"label": "商家编码", "required": False, "kind": "text", "maxLength": 64},
        {"label": "商品名称", "required": True, "kind": "text", "role": "name", "maxLength": 42},
        {"label": "上架范围", "required": True, "kind": "select", "role": "scope",
         "options": ["全域自提点"], "default": "全域自提点"},
        {"label": "重量(g)", "required": False, "kind": "number", "in": "sku-row",
         "emptyBlocksSubmit": True, "default": ""}])
    mp = dump_file("b-misattribute.json", misattribution)
    res = diff(base_path, mp, "--check-profile", gp)
    check("profile matches the probe" in res.stdout,
          "the name rule is checked against the field marked role 'name', not against the first text input "
          "that happens to carry a maxLength")
    longer = probe_dump(capturedAt="2026-11-04T12:00:00+08:00", fields=[
        {"label": "商家编码", "required": False, "kind": "text", "maxLength": 42},
        {"label": "商品名称", "required": True, "kind": "text", "role": "name", "maxLength": 60},
        {"label": "上架范围", "required": True, "kind": "select", "role": "scope",
         "options": ["全域自提点"], "default": "全域自提点"},
        {"label": "重量(g)", "required": False, "kind": "number", "in": "sku-row",
         "emptyBlocksSubmit": True, "default": ""}])
    res = diff(base_path, dump_file("b-longername.json", longer), "--check-profile", gp)
    check("profile name maxLength 42 but observed 60 on '商品名称'" in res.stdout,
          "a drifted name limit is attributed to the name field, with its label shown")

    leafroot = json.loads(gp.read_text(encoding="utf-8"))
    leafroot["defaults"]["rootCategory"] = "泡面"
    lr = tmp / "profile-leafroot.json"
    lr.write_text(json.dumps(leafroot, ensure_ascii=False, indent=1), encoding="utf-8")
    res = diff(base_path, same_path, "--check-profile", lr)
    check("not as a root" in res.stdout,
          "a leaf is not a root: matching any label in the tree would let the cascader start a level too low")

    twin = probe_dump(capturedAt="2026-11-01T10:00:00+08:00", fields=[
        {"label": "商品名称", "required": True, "kind": "text", "role": "name", "maxLength": 42},
        {"label": "商家编码", "required": False, "kind": "text"},
        {"label": "商家编码", "required": False, "kind": "text"}])
    one_twin = probe_dump(capturedAt="2026-11-04T12:00:00+08:00", fields=[
        {"label": "商品名称", "required": True, "kind": "text", "role": "name", "maxLength": 42},
        {"label": "商家编码", "required": False, "kind": "text"}])
    res = diff(dump_file("b-twin.json", twin), dump_file("b-onetwin.json", one_twin))
    check("- field 商家编码#2" in res.stdout and "more than one control" in res.stdout,
          "two controls under one label are numbered: losing one is drift, not a no-op overwrite")

    for slot, (label, broken) in enumerate((("skuColumns 混入 dict", {"skuColumns": [{"col": "价格"}, "库存"]}),
                                            ("字段项是字符串", {"fields": ["商品名称"]}),
                                            ("observations 写成一句话", {"observations": "重量留空会拦住提交"}),
                                            ("类目节点写成字符串", {"categoryTree": {"酒水饮料": "茶饮料"}}))):
        res = diff(base_path, dump_file(f"b-broken-{slot}.json", probe_dump(**broken)))
        check(res.returncode == 2 and "Traceback" not in res.stderr,
              f"an unusable dump is refused with a message, not a traceback: {label}")

    undated = {k: v for k, v in probe_dump().items() if k != "capturedAt"}
    res = diff(dump_file("b-undated-a.json", undated), dump_file("b-undated-b.json", undated))
    check(res.returncode == 2 and "no capturedAt" in res.stderr,
          "two undated dumps cannot claim 'no drift': nothing proves they are two moments")

    sectionless = json.loads(gp.read_text(encoding="utf-8"))
    sectionless["boards"] = {"pickup": {"label": "到店自提", "defaults": {"rootCategory": "酒水饮料"}}}
    sl = tmp / "profile-otherboard.json"
    sl.write_text(json.dumps(sectionless, ensure_ascii=False, indent=1), encoding="utf-8")
    express_before = dump_file("b-express-a.json", probe_dump(board="express",
                                                              capturedAt="2026-11-01T10:00:00+08:00"))
    express_after = dump_file("b-express-b.json", probe_dump(board="express",
                                                             capturedAt="2026-11-04T12:00:00+08:00"))
    res = diff(express_before, express_after, "--check-profile", sl)
    check(res.returncode == 1 and "only declares ['pickup']" in res.stdout,
          "a profile that declares sections but not this one says so instead of silently using the defaults")
    sectionless["boards"]["express"] = {"label": "快递发货",
                                        "defaults": {"rootCategory": "不存在的根"},
                                        "fields": {"name": {"maxLength": 99}}}
    sl.write_text(json.dumps(sectionless, ensure_ascii=False, indent=1), encoding="utf-8")
    res = diff(express_before, express_after, "--check-profile", sl)
    check("root category '不存在的根' is not in" in res.stdout and "name maxLength 99 but observed 42" in res.stdout,
          "once the section is declared, its own overrides are the ruler that gets checked")

    no_scope_doc = probe_dump(capturedAt="2026-11-04T11:00:00+08:00",
                              fields=[f for f in probe_dump()["fields"] if f.get("role") != "scope"])
    no_scope = dump_file("b-noscope.json", no_scope_doc)
    res = diff(base_path, no_scope, "--check-profile", gp)
    check("observed no field with role 'scope'" in res.stdout,
          "a profile that fills a scope the probe never saw is called out, not quietly skipped")
    boards_prof = json.loads(gp.read_text(encoding="utf-8"))
    boards_prof["defaults"]["rootCategory"] = "生鲜"
    boards_prof["fields"]["name"]["maxLength"] = 80
    boards_prof["fields"]["spec"]["skuFieldOrder"] = ["price", "stock"]
    boards_prof["fields"]["scope"]["value"] = "全域自提点"
    boards_prof["boards"] = {"pickup": {"label": "到店自提",
                                        "defaults": {"rootCategory": "酒水饮料"},
                                        "fields": {"name": {"maxLength": 42},
                                                   "spec": {"skuFieldOrder": ["price", "stock", "points",
                                                                              "weight", "listed"]}}}}
    bp = tmp / "profile-boards.json"
    bp.write_text(json.dumps(boards_prof, ensure_ascii=False, indent=1), encoding="utf-8")
    res = diff(base_path, same_path, "--check-profile", bp)
    check(res.returncode == 0 and "profile matches the probe" in res.stdout,
          "a section's own overrides are the ruler for its probe: no false complaint about the top level")
    boards_prof["boards"]["pickup"]["fields"]["without"] = ["scope"]
    bp.write_text(json.dumps(boards_prof, ensure_ascii=False, indent=1), encoding="utf-8")
    res = diff(base_path, no_scope, "--check-profile", bp)
    check("observed no field with role 'scope'" not in res.stdout,
          "fields.without is honoured: this section really dropped the field, so its probe has no scope")

    res = diff(base_path, same_path, "--check-profile", gp, "--verbose", "--out", tmp / "drift.json")
    check("unchanged:" in res.stdout and (tmp / "drift.json").exists(),
          "--verbose counts what did not change and --out writes the report")

    single = subprocess.run([sys.executable, str(SCRIPTS / "diff_probe.py"), str(same_path),
                             "--check-profile", str(gp)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    check(single.returncode == 0 and "checking one probe" in single.stdout
          and "profile matches the probe" in single.stdout,
          "the first probe has no history to diff against, but it can already be held against the profile")
    lone = subprocess.run([sys.executable, str(SCRIPTS / "diff_probe.py"), str(same_path)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    check(lone.returncode == 2 and "answers nothing" in lone.stderr,
          "one dump without a profile is refused rather than reported as a clean bill")
    three = subprocess.run([sys.executable, str(SCRIPTS / "diff_probe.py"), str(base_path), str(base_path),
                            str(same_path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    check(three.returncode == 2, "three dumps is a usage error, not a silent choice of two")

    half = tmp / "half.json"
    half.write_text(json.dumps({"capturedAt": "x", "board": "p"}, ensure_ascii=False), encoding="utf-8")
    res = diff(base_path, half)
    check(res.returncode == 2 and "missing sections" in res.stderr, "a dump missing sections is refused")
    sneaky = probe_dump(**{"fields": [{"label": "商品名称", "required": True, "kind": "text", "maxLength": 42},
                                      {"adminToken": "abc"}]})
    sneaky_path = dump_file("sneaky.json", sneaky)
    res = diff(base_path, sneaky_path)
    check(res.returncode == 2 and "credential" in res.stderr,
          "a dump carrying something credential-shaped is refused, not echoed")
    res = diff(base_path, tmp / "nope.json")
    check(res.returncode == 2, "a missing dump file is refused")

    source = (SCRIPTS / "diff_probe.py").read_text(encoding="utf-8")
    check(not any(tok in source for tok in ("urllib", "socket", "requests", "http.client")),
          "diff_probe compares files only - no network module in the source")
    example = json.loads((ROOT / "skills" / "auto-list-products" / "probe-dump.example.json").read_text(encoding="utf-8"))
    check(set(("capturedAt", "board", "fields", "categoryTree", "separator", "trays", "skuColumns", "submit"))
          <= set(example), "probe-dump.example.json documents every section the script reads")
    example_path = dump_file("example-as-dump.json", example)
    example_next = dump_file("example-next.json", {**example, "capturedAt": "2026-12-01T09:00:00+08:00",
                                                   "observations": list(example.get("observations") or [])
                                                                   + ["详情页新增一张长图槽"]})
    res = diff(example_path, example_next)
    check(res.returncode == 1 and "+ observation 详情页新增一张长图槽" in res.stdout,
          "the shipped example is a dump the script can actually read and compare, not a picture of one")


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
        test_boards(tmp)
        test_prepare_images(tmp)
        test_diff_probe(tmp)
    if failures:
        print(f"\n{len(failures)} smoke failures:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nsmoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
