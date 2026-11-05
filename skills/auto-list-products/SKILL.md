---
name: auto-list-products
description: >-
  Discovery-first bulk product listing for any web admin backend. Use when the user wants to 批量上架商品 /
  自动填上架表单 / 把商品截图批量发布到商城后台, or to make an existing listing workflow work on a new
  platform. The agent probes the user's own admin form and writes a platform profile (fields, category
  tree, defaults, verification hooks), then runs a resumable queue: square image crops with blank and dot
  guards, fact extraction, optional registration sheet, browser filling, per-field verification, submit,
  audit.
---

# Auto List Products

Turn a folder of product screenshots (or photos plus a price list) into submitted listings on the
user's own admin backend.

Nothing about a platform is hardcoded. The agent **probes the user's live form once**, writes a profile,
has the user confirm it, and from then on drives listings from that profile.

## Phase 0 — Preflight (must pass before any work)

### 1. Can the agent control the user's browser?

The workflow needs the user's real, logged-in browser. Verify, don't assume:

- ask the agent for its browser list / user tabs. An empty list or a "connector not ready / extension
  missing / update required" error means there is **no working bridge** — stop and help the user install
  and connect the browser-control extension, then re-check.
- never continue in a different browser surface that merely shows the same site: it is a separate,
  logged-out session and the batch will fail at submit time.
- confirm the agent can read page semantics (accessibility tree / locators) and click, not only screenshot.

### 2. Can it produce real input events?

Needed for the native file dialog and for controls that ignore scripted clicks. If unavailable, only
continue when the uploader exposes a plain file input; otherwise stop and tell the user.

### 3. Local tooling

```bash
python -c "import PIL, openpyxl; print('ok')" || pip install pillow openpyxl
```

### 4. Session and mode

- the tab must be logged in; if a login page appears, hand the tab to the user (never type credentials);
- agree the mode for this run: `fill-only` or `fill-and-submit`. Do not switch on your own.

No backend to rehearse against yet? The repository ships `sandbox/admin.html` - a local, offline, zero-dependency
practice form with the same shapes this skill exists to handle (a required column with no asterisk, category
nodes that share a label, pre-filled defaults, a confirm dialog, a list snapshot in the documented shape).
Run the whole chain there first; `sandbox/README.md` lists every trap it plants.

## Phase 1 — Probe the platform, write the profile

Do this once per backend (and again after the admin UI changes). Read
`references/probe-checklist.md`; follow it in order.

The probe answers four questions by inspecting the live create form:

1. **Where is the form** and where is the list? (routes, not guesses) If the backend has **sections**
   (self-pickup vs courier, retail vs wholesale - two forms under one login), probe each one as its own
   thing: its create URL, its list URL, its category root, the fields it does not have, its SKU column
   order, and the numbers that differ per section. Record them under `boards.<key>`; a single-section
   backend simply has no `boards` block.
2. **Which fields exist and which are required**, with one stable locator each (label/placeholder/role
   text, not brittle CSS classes).
3. **What is the category tree**, dumped level by level, and how the selected path reads back
   (including the separator used in the read-back string).
4. **How is success proven** — where the stored image URL appears, which preview echoes the points, what
   the confirmation dialog says, and what the URL looks like when the submit really landed.

Every upload slot on the form becomes one entry in `image.trays`: the product image, the detail images
(their own tile, often with a different ratio rule), and a per-spec-row image cell when the form has one.
For each, record `crop` (`square` opens a crop dialog that needs a confirm button, `any` accepts the file
as-is, `none` means no dialog), `maxCount`, `bind` (`product` or `sku`) and whether an empty slot blocks
the submit.

**Write down what you saw before you write down what you decided.** The observation goes into a probe dump
(`probe-dump.example.json`) per board, named with its capture time: the whole category tree node by node,
every field with the required marker the page shows *and* the behaviour you measured, the slots, the SKU
column order, the submit wording, and the notes. `profile.json` is derived from it afterwards. The dump
carries what the profile has no place for - the nodes you did not route anything to, the column order, the
field that looks optional and is not - and it is the only thing that can later answer
"did the platform change?". On the practice sandbox `window.__alpProbeDump('pickup')` writes one from the
live DOM; on a real backend you record the same sections by reading the page.

Then ask the user the business questions the page cannot answer:

- which scope/region option to use by default,
- stock quantity, spec name, and the root category for their products,
- whether points exist and how they are derived, whether display counters should be filled,
- whether a case SKU row is wanted, and how that row is labelled on their platform (a template such as
  `{per}{unit}*{cu}` — the case-pack word is theirs, not this skill's),
- any minimum coupon / skip rule (default: none),
- fill-only or fill-and-submit.

Write `profile.json` (shape: `profile.example.json`), run the validator, hold the profile against the dump
it was derived from, then fill **one** product with the profile before building a queue:

```bash
python scripts/check_profile.py profile.json
python scripts/diff_probe.py probe-pickup-2026-11-04.json --check-profile profile.json
```

The second command checks the profile's claims about the form (root category, scope option, SKU column
count, separator, slot keys and their `maxCount`/`crop`, name length) against what the probe recorded.
A copy error between observation and profile is the cheapest bug to find and the most expensive one to
carry into a 500-row batch.

## Phase 2 — Image intake

Two different problems wear the same name. Pick the right tool:

**Screenshots of products** - crop one square image per product with `scripts/crop_square.py`, mapping each
source resolution to the photo area (`--windows "1440x3200:285,1280x2844:280:1500"`; phone screenshots carry
a fixed title bar, and a bottom bound is white-padded back to a square):

- blank-crop detection (the photo never rendered in the screenshot) — a blank crop is **never written to
  disk**; the entry automatically falls back to its `alt_src` duplicate screenshot,
- carousel-dot detection: dots at the bottom edge are capped off and white-padded back to square.

**Assets the product already has** (a square per colour, a few scene shots, a 3:4 spec infographic) -
assemble them into the form's slots with `scripts/prepare_images.py`. Slot counts and crop rules come from
`profile.image.trays`; the order comes from the plan (`products-images.example.json`):

```bash
python scripts/prepare_images.py --profile profile.json --products products.json \
    --src assets --dst out --manifest manifest.json --sheet sheet.jpg
```

Three rules this tool enforces because they were learned expensively:
- **centre crop is the only crop** - never pad, never blur, never add a background;
- **never upscale** - a small source stays small and says `not-upscaled`;
- **never invent a slot** - a short set reports `missing: n` instead of repeating an image, and using one
  file twice in a slot group is refused outright.

Read the reports either way; for every blank or unusable entry use another screenshot or ask the user to
re-send it. Eyeball the contact sheet before uploading anything.

## Phase 3 — Fact extraction

Record per product: `n`, title (without case pack), `size` + `unit`, `per` units per case, `supply` case
price, `retail` price per unit, `cat` key, `img` file; add `cu` (the case-pack word) when the profile's
`caseSkuLabel` uses `{cu}`. Unreadable values are listed and asked, never guessed.

Sanity-check arithmetic: a bundle is one unit (cost = single-pack cost × packs per bundle); a case's unit
cost is case price ÷ units per case; a negative or absurd margin stops the batch.

Write `products.json` (shape in `scripts/make_queue.py --help`).

## Phase 4 — Optional registration sheet

Only if the user keeps one: append rows with `scripts/fill_xlsx.py`. The first empty row is detected from
the name column (`max_row` lies on sheets that end in styled blanks). Price columns stay numeric (case pack
via `number_format`, never text), derived columns stay formulas, the workbook is flagged for recalc on open
and backed up first. Rows with no supply price write a literal coupon and leave those two cells empty.

## Phase 5 — Queue

`scripts/make_queue.py --profile profile.json` builds the jobs. The profile is the only source of business
behaviour, so nothing has to be re-typed per batch:

- root category and the path separator come from `defaults.rootCategory` / `fields.category.expectedSeparator`,
  and each item carries the exact `expectCat` string the fill step asserts against;
- one SKU row per unit, plus a case row built from `fields.spec.caseSkuLabel` unless `business.caseSku` is off;
- stock and spec name ride on every item from `defaults`, so the browser step reads one object and has all
  of its numbers;
- points follow `business.pointsMode` (`none` / `coupon` / `fixed`), display counters follow
  `business.counters`, the coupon floor follows `business.minCoupon`;
- same name + same retail is dropped as a duplicate; same name + different price is kept and reported;
- a missing value, an unknown category key or an impossible coupon aborts the run without writing a queue;
- `--board pickup` is more than a tag when the profile has `boards`: that section's category root, field
  set, SKU column order, pre-checked controls and `couponOffset` are merged over the top level, every item
  is tagged, and a run is built per section - never one queue pretending both sections share a ruler.
  An offset that pushes a coupon to zero or below aborts the run naming the product.

Flags (`--root`, `--points`, `--min-coupon`, `--display-counters`, `--no-case-sku`, `--stock`,
`--spec-name`, `--board`, `--only`) exist only to override the profile for a single run.

## Phase 6 — Fill loop

Follow `references/browser-playbook.md`, driven by `profile.json`. Per product:

1. open a fresh form from `form.newUrl`;
2. fill each `image.trays` entry bound to the product — upload, commit the crop dialog when that tray is
   `crop: square`, then verify the stored object's byte size against `image.minBytes` (a white crop is a few
   KB — delete, retry, then abort); tray-bound-to-`sku` cells get one image per spec row;
3. fill name → category → scope → spec name → spec values → SKU rows (optional fields only as configured);
4. read the form back into `actual.json` (`actual.example.json`) and run the pre-submit gate:

   ```bash
   python scripts/verify_item.py --profile profile.json --queue queue.json --n 7 --actual actual.json --board express
   ```

   It checks name, the exact category path, scope, spec name, spec-value order, SKU row count, price,
   stock and points per row, display counters, every required upload slot against `image.minBytes`, the
   pre-checked control count, and the agreed submit mode. Every column named in
   `fields.spec.skuFieldOrder` must carry a value on every row - a page refuses an empty weight as firmly as
   an empty price, and a gate that only checks the columns it happens to know about is what let a real run
   pass the gate and then get rejected by the form. Exit 1 means **do not submit** - abort the item,
   keep the verdict, report it; never "click anyway because it looks right".

   The gate refuses to bless what it did not compare: a form holding a spec value the queue item never
   declared fails. Products with real variants (colours, sizes) declare every one of them in the item's
   `specValues` - then each row is matched by name - or the run passes `--allow-extra-specs`, which still
   prints which rows went unchecked.
5. submit only in `fill-and-submit` mode, accept the confirmation, assert the route changed;
6. persist progress after each success; stop the batch on the first abort and report it.

Verification strength must not drift between product 1 and product 120: that is the whole reason the gate
is a script rather than a judgement call.

`state.json` is the resume record, one entry per attempted item:

```json
{
  "queueFile": "queue.json",
  "profile": "profile.json",
  "items": [
    {"n": 1, "status": "submitted", "attempts": 1, "note": ""},
    {"n": 2, "status": "aborted", "attempts": 2, "note": "category read back as the wrong leaf"}
  ]
}
```

An item is only ever `submitted` after its route assertion passed; resume by skipping those and re-running
the rest.

## Phase 7 — Audit

A batch is not finished when the last submit returned; it is finished when the shop and the plan agree.

1. Scrape the product list into a snapshot file (`listings.json`, shape in `listings.example.json`): every
   page, the real row count in `total`, the status wording exactly as the page shows it, one entry per
   product with its spec rows and prices. Follow `references/browser-playbook.md` § List snapshot.
2. Reconcile the snapshot against the plan:

   ```bash
   python scripts/reconcile.py --expected queue.json --listings listings.json --board pickup --out diff.json
   ```

   The expected side can be `queue.json` (name + price + spec structure) or the registration workbook
   (name + price + coupon; a sheet has no spec names, so the structure dimension is reported as skipped, not
   passed). The script reads files only - it never touches the backend.
3. Act on the four lists: **missing** (re-list), **price** (fix or accept, with both numbers shown),
   **structure** (the case-only / wrong-spec shape that name-only checks hide), **duplicate** (delete the
   extra), plus the **status** list of rows that exist but are not live. Exit code 1 means differences; do
   not call a batch done on a green-looking run that exited 1.
4. Re-probe and diff, when anything about the form stopped making sense: a submit the page refused without
   explaining, a batch that behaved differently from its pilot, or the first run of a new working session on
   the same backend. Export a fresh dump for that board and compare it with the one the profile came from:

   ```bash
   python scripts/diff_probe.py probe-pickup-2026-10-31.json probe-pickup-2026-11-04.json --check-profile profile.json
   ```

   Exit 1 here means the platform moved - the category a queue row is routed to may no longer exist, a slot
   may take fewer images, a column may have been inserted in the middle of the SKU row. Re-read the
   checklist, re-write the profile, re-make the queue. Do not patch queue rows by hand to make a diff pass.
   Two dumps of different boards are refused unless you say `--cross-board`, and two dumps of the same
   capture are refused outright: neither can answer "did the platform change".
5. Spot-check hosted images with `scripts/check_images.py --urls`.
6. Report submitted / skipped / retried / reconciled, and quote the residual diff.

`state.json` is a work log, not evidence. Whether a product exists is answered by the snapshot and the
reconcile, because a submit that failed, an expired session, or a reviewed-and-frozen listing all leave
`state.json` claiming success.

## Safety

- Never invent prices, specs or business rules. Never handle credentials.
- A probe dump records what the form looks like, never what the session is worth: no cookies, tokens or
  passwords in it. `diff_probe.py` refuses a dump that carries a key shaped like one.
- Never bypass uploads, review, rate limits or anti-bot checks with page scripts.
- Keep the user's tab alive when pausing (mark it as handoff) so a filled form is not lost.

## Resources

- `profile.example.json` — the profile the probe produces (upload trays, locators, defaults, business policy).
- `listings.example.json` — the snapshot the audit reads back from the product list.
- `actual.example.json` — what to read off the filled form before the submit gate runs.
- `probe-dump.example.json` — what the form looked like when it was probed, before any decision was made.
- `scripts/check_profile.py` — refuse a half-probed profile before touching products.
- `scripts/crop_square.py` — square crops, blank + dot guards with `alt_src` fallback, contact sheet.
- `scripts/prepare_images.py` — lay existing assets into the form's slots: centre crop only, never upscale, never fill a short slot.
- `scripts/check_images.py` — blank-image audit for local files or hosted URLs.
- `scripts/make_queue.py` — facts + profile → queue (root, case label, points, counters, floor, stock).
- `scripts/verify_item.py` — the pre-submit gate: one PASS/FAIL/SKIP line per rule, exit 1 blocks the submit.
- `scripts/fill_xlsx.py` — append rows without breaking formulas.
- `scripts/reconcile.py` — plan vs shop: missing / duplicate / price / structure, plus status and extras.
- `scripts/diff_probe.py` — probe vs probe, and profile vs probe: what moved, and what each move breaks.
- `products-images.example.json` — the plan for assembling assets a product already has.
- `references/probe-checklist.md` — the discovery procedure behind Phase 1.
- `references/browser-playbook.md` — uploads, real input events, handoff, window hygiene.
- `references/pitfalls.md` — failure catalogue with fixes.
