---
name: auto-list-products
description: Discovery-first bulk product listing for any web admin backend. Use when the user wants to 批量上架商品 / 自动填上架表单 / 把商品截图批量发布到商城后台, or to make an existing listing workflow work on a new platform. The agent first probes the user's own admin form and writes a platform profile (fields, category tree, defaults, verification hooks), then runs a resumable queue: square image crops with blank/dot guards, fact extraction, optional registration sheet, browser filling, per-field verification, submit, audit.
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
3. **What is the category tree**, dumped level by level, and how the selected path reads back.
4. **How is success proven** — where the stored image URL appears, which preview echoes the points, what
   the confirmation dialog says, and what the URL looks like when the submit really landed.

Then ask the user the business questions the page cannot answer:

- which scope/region option to use by default,
- stock quantity, spec name, and the root category for their products,
- whether points exist and how they are derived, whether display counters should be filled,
- whether a case SKU row is wanted,
- any minimum coupon / skip rule (default: none),
- fill-only or fill-and-submit.

Write `profile.json` (shape: `profile.example.json`), run the validator, then fill **one** product with the
profile before building a queue:

```bash
python scripts/check_profile.py profile.json
```

## Phase 2 — Image intake

Crop one square image per product with `scripts/crop_square.py`:

- per-resolution crop tops (phone screenshots carry a fixed title bar),
- blank-crop detection (the photo never rendered in the screenshot),
- carousel-dot detection: dots at the bottom edge are capped off and white-padded back to square.

Read the report; for every blank entry use a duplicate screenshot (`alt_src`) or ask the user to re-send
it. Eyeball the contact sheet before uploading anything.

## Phase 3 — Fact extraction

Record per product: title (without case pack), size + unit, units per case, retail price per unit, case
supply price, image file, category key. Unreadable values are listed and asked, never guessed.

Sanity-check arithmetic: a bundle is one unit (cost = single-pack cost × packs per bundle); a case's unit
cost is case price ÷ units per case; a negative or absurd margin stops the batch.

Write `products.json` (shape documented in `scripts/make_queue.py --help`).

## Phase 4 — Optional registration sheet

Only if the user keeps one: append rows with `scripts/fill_xlsx.py`. Price columns stay numeric (case pack
via `number_format`, never text), derived columns stay formulas, workbook backed up first.

## Phase 5 — Queue

`scripts/make_queue.py` turns facts into resumable jobs. All business behaviour comes from the profile:

- category path from `categories.json`, re-checked against the live tree at fill time;
- one SKU row per unit, plus a case row only if the profile wants one;
- points only if the profile defines them (`--points-as-coupon` or per-product values);
- display counters only with `--display-counters`;
- skip rule only with `--min-coupon`.

## Phase 6 — Fill loop

Follow `references/browser-playbook.md`, driven by `profile.json`. Per product:

1. open a fresh form from `form.newUrl`;
2. upload product + detail images, then verify the stored object's byte size (a white crop is a few KB —
   retry, then abort);
3. fill name → category → scope → spec name → spec values → SKU rows (optional fields only as configured);
4. verify every field against the queue; any mismatch aborts the item;
5. submit only in `fill-and-submit` mode, accept the confirmation, assert the route changed;
6. persist progress after each success; stop the batch on the first abort and report it.

## Phase 7 — Audit

- rescan the list for duplicates and missing items;
- spot-check hosted images with `scripts/check_images.py --urls`;
- report submitted / skipped / retried and anything still pending.

## Safety

- Never invent prices, specs or business rules. Never handle credentials.
- Never bypass uploads, review, rate limits or anti-bot checks with page scripts.
- Keep the user's tab alive when pausing (mark it as handoff) so a filled form is not lost.

## Resources

- `profile.example.json` — the profile the probe produces.
- `scripts/check_profile.py` — refuse a half-probed profile before touching products.
- `scripts/crop_square.py` — square crops, blank + dot guards, contact sheet.
- `scripts/check_images.py` — blank-image audit for local files or hosted URLs.
- `scripts/make_queue.py` — facts → queue, with optional points / counters / skip rule.
- `scripts/fill_xlsx.py` — append rows without breaking formulas.
- `references/probe-checklist.md` — the discovery procedure behind Phase 1.
- `references/browser-playbook.md` — uploads, real input events, handoff, window hygiene.
- `references/pitfalls.md` — failure catalogue with fixes.
