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

## Phase 1 — Probe the platform, write the profile

Do this once per backend (and again after the admin UI changes). Read
`references/probe-checklist.md`; follow it in order.

The probe answers four questions by inspecting the live create form:

1. **Where is the form** and where is the list? (routes, not guesses)
2. **Which fields exist and which are required**, with one stable locator each (label/placeholder/role
   text, not brittle CSS classes).
3. **What is the category tree**, dumped level by level, and how the selected path reads back
   (including the separator used in the read-back string).
4. **How is success proven** — where the stored image URL appears, which preview echoes the points, what
   the confirmation dialog says, and what the URL looks like when the submit really landed.

Every upload slot on the form becomes one entry in `image.trays`: the product image, the detail images
(their own tile, often with a different ratio rule), and a per-spec-row image cell when the form has one.
For each, record `crop` (`square` opens a crop dialog that needs a confirm button, `any` accepts the file
as-is, `none` means no dialog), `maxCount` and `bind` (`product` or `sku`).

Then ask the user the business questions the page cannot answer:

- which scope/region option to use by default,
- stock quantity, spec name, and the root category for their products,
- whether points exist and how they are derived, whether display counters should be filled,
- whether a case SKU row is wanted, and how that row is labelled on their platform (a template such as
  `{per}{unit}*{cu}` — the case-pack word is theirs, not this skill's),
- any minimum coupon / skip rule (default: none),
- fill-only or fill-and-submit.

Write `profile.json` (shape: `profile.example.json`), run the validator, then fill **one** product with the
profile before building a queue:

```bash
python scripts/check_profile.py profile.json
```

## Phase 2 — Image intake

Crop one square image per product with `scripts/crop_square.py`, mapping each source resolution to the
photo area of the screenshot (`--windows "1440x3200:285,1280x2844:280:1500"`; phone screenshots carry a
fixed title bar, and a bottom bound is white-padded back to a square):

- blank-crop detection (the photo never rendered in the screenshot) — a blank crop is **never written to
  disk**; the entry automatically falls back to its `alt_src` duplicate screenshot,
- carousel-dot detection: dots at the bottom edge are capped off and white-padded back to square.

Read the report; for every entry that is still blank or unusable, use another screenshot or ask the user to
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
- `--board pickup` tags every item with the section of the shop it belongs to, so a shop with separate
  self-pickup and courier sections is audited one section at a time.

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
   python scripts/verify_item.py --profile profile.json --queue queue.json --n 7 --actual actual.json
   ```

   It checks name, the exact category path, scope, spec name, spec-value order, SKU row count, price,
   stock and points per row, display counters, every required upload slot against `image.minBytes`, the
   pre-checked control count, and the agreed submit mode. Exit 1 means **do not submit** - abort the item,
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
4. Spot-check hosted images with `scripts/check_images.py --urls`.
5. Report submitted / skipped / retried / reconciled, and quote the residual diff.

`state.json` is a work log, not evidence. Whether a product exists is answered by the snapshot and the
reconcile, because a submit that failed, an expired session, or a reviewed-and-frozen listing all leave
`state.json` claiming success.

## Safety

- Never invent prices, specs or business rules. Never handle credentials.
- Never bypass uploads, review, rate limits or anti-bot checks with page scripts.
- Keep the user's tab alive when pausing (mark it as handoff) so a filled form is not lost.

## Resources

- `profile.example.json` — the profile the probe produces (upload trays, locators, defaults, business policy).
- `listings.example.json` — the snapshot the audit reads back from the product list.
- `actual.example.json` — what to read off the filled form before the submit gate runs.
- `scripts/check_profile.py` — refuse a half-probed profile before touching products.
- `scripts/crop_square.py` — square crops, blank + dot guards with `alt_src` fallback, contact sheet.
- `scripts/check_images.py` — blank-image audit for local files or hosted URLs.
- `scripts/make_queue.py` — facts + profile → queue (root, case label, points, counters, floor, stock).
- `scripts/verify_item.py` — the pre-submit gate: one PASS/FAIL/SKIP line per rule, exit 1 blocks the submit.
- `scripts/fill_xlsx.py` — append rows without breaking formulas.
- `scripts/reconcile.py` — plan vs shop: missing / duplicate / price / structure, plus status and extras.
- `references/probe-checklist.md` — the discovery procedure behind Phase 1.
- `references/browser-playbook.md` — uploads, real input events, handoff, window hygiene.
- `references/pitfalls.md` — failure catalogue with fixes.
