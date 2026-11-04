# Changelog

## 0.6.0 — 2026-11-04

The other half of image intake: a product that arrives with images already, instead of a screenshot.

- `scripts/prepare_images.py` lays existing assets (a square per colour, scene shots, a 3:4 infographic) into
  the backend's upload slots. Slot counts and crop rules come from `profile.image.trays`
  (`maxCount`, `crop`: `square` centre-crops, `any`/`none` upload as-is); the order comes from the plan
  (`products-images.example.json`), and the manifest says which reason decided each order.
- Three rules that were learned expensively are now enforced by code rather than by intent:
  **centre crop only** - never pad, never blur, never add a background; **never upscale** - a small source
  stays small and says `not-upscaled`; **never invent a slot** - a short set reports
  `short: {tray, missing}` and using one file twice in a slot group is refused.
- A cross-review of the first draft found a family of silent successes - none of them caught by the 24
  assertions that were green - and each is now fixed with its own test: a product with no candidates
  reported "every slot filled"; two products sharing a number wrote to the same filename and overwrote each
  other while stdout counted both; a tray missing `maxCount` silently became a one-slot tray; a variant
  without a label wrote the image but lost the SKU binding; a tray named `short` or `suspect` overwrote the
  manifest's own fields (and one named `suspect` crashed); `single` only worked when the tray was literally
  called `main`; `not-upscaled` was skipped for non-square sources; a control character in a label produced
  an error naming the wrong file; a contact sheet was announced when nothing had been written; and an
  exit-2 run still wrote its manifest, contradicting the design.
- Contract tightened accordingly: every tray key always appears in the manifest (possibly empty), product
  numbers and tray keys must be unique and non-reserved, tray definitions are validated before anything is
  written, exit 2 writes nothing at all, and the SKU binding lives in `skuImages` so it cannot collide with
  the conventional `sku` tray key.
- `SKILL.md` Phase 2 splits into the two shapes of intake; the failure catalogue gained "padded or blurred
  to make a square" and "a short image set got filled up"; the probe checklist records per-slot counts and
  the square requirement as the input to this tool.
- `tests/smoke.py` grew to 202 assertions, Linux and Windows both green.

## 0.5.0 — 2026-11-04

Back offices with sections - self-pickup and courier, retail and wholesale - are one login but not one form.

- `profile.json` gained an optional `boards` block: per section its own create/list URLs, category root,
  field set (`without: ["scope"]`), SKU column order, pre-checked controls and `couponOffset`.
  A profile without it behaves exactly as before, and nothing about a section is hardcoded.
- `--board <key>` became the switch instead of a label. `make_queue` merges that section over the top level,
  tags every item, keeps retail prices fixed while moving the coupon and what derives from it, and aborts
  naming the product when an offset pushes a coupon to zero or below. `verify_item` measures with that
  section's ruler - a field the section lacks is skipped with the section named, a column it demands
  (weight, volume) is required - so a courier form is never judged by the pickup checklist. Both refuse an
  unknown key with exit 2 and list the real ones.
- `check_profile.py` validates each section (label, complete form pair, known override keys, numeric offset)
  and prints them side by side, so what the user confirms is two shapes rather than one blurred profile.
  The top-level `form.newUrl` is now satisfied by the top level or by every section.
- The sandbox grew the second section for real: the courier form drops the scope picker for a static
  shipping note, adds a required volume column, and ships with the listed toggle off; lists and snapshots
  are per section, and the seed covers both. `sandbox/README.md` documents all of it, and
  `tests/sandbox_static.py` now matches trap definitions rather than error wording.
- probe checklist step 1 gained the section inventory - repeat the routes per section and diff the two forms
  on screen, because "same form, different data" is exactly the assumption that fills a courier product
  with the pickup ruler.
- `tests/smoke.py` grew to 165 assertions (26 of them for sections), Linux and Windows both green.

## 0.4.0 — 2026-11-04

The flow became runnable without a real store, and the evals became an experiment instead of a wish.

- `sandbox/admin.html`: a zero-dependency, offline, single-file practice back office - routes for create/list/
  recycle, a three-level cascader, a spec matrix with SKU rows, three upload slots that report stored bytes,
  a confirm dialog, and a list snapshot rendered in the `listings.json` shape. Deterministic seed, reset button,
  no network calls, and it says on the page that it is not a real store.
- `sandbox/README.md`: the seed table and **every planted trap documented** - an unmarked required column
  (重量), category nodes that share a label (`食品/零食` → `食品`, and `速食食品` containing `食品`), pre-filled
  stock, the confirm step - because an undocumented trap would make a comparison run irreproducible.
- `evals/run-book.md`: how to actually run the double-blind. The only variable is whether SKILL.md is injected;
  isolated browser contexts; four axes; evidence per axis; `未执行` instead of guessing; a precondition that the
  environment must let the agent drive a browser; and §4.1 - **premise state has to be built in the same
  context**, otherwise "nothing to submit" gets recorded as "held the line".
- `evals/results/2026-11-04-sandbox.md`: the first recorded run. The A/B comparison came out **not comparable**
  (the baseline side was blocked from the browser by the permission classifier; the with-skill side's failure was
  an unfilled cell, not a caught mistake) and is written up that way. The rehearsal section records the chain
  running end to end: probe → `check_profile` exit 0 → queue → live form → gate exit 1 on empty points → fill →
  gate exit 0 → submit → snapshot → `reconcile` reporting the unsubmitted product as MISSING and the submitted
  one as pending-review.
- Seven fixes that run produced, each with a regression assertion:
  `make_queue` no longer demands `supply`/`per` unless something consumes them; `categories.json` accepts a
  two-level path; a per-key `l1` overrides the root for catalogues whose first level varies; `verify_item`
  requires a value in every column named by `skuFieldOrder` (this is the one that let a real run pass the gate
  and then get refused by the form); category paths compare segment by segment so a `>` breadcrumb is not a
  false block; `image.minBytes: 0` means no floor; non-UTF-8 input exits 2 and a failed `--out` warns instead of
  rewriting the verdict's exit code.
- Contract clarified after a false block of my own making: `actual.json`'s `checked` counts **form-level**
  pre-ticked controls only; SKU-row toggles belong in `skuRows[].listed`.
- Sandbox fixes: panels built before the element was in the document, a `var` loop closure that made all three
  cascader levels read as level 3, a missing per-row image slot that silently aborted the submit, and a stock
  `placeholder` that looked like a default value.
- `tests/sandbox_static.py`: 25 offline checks (no network tokens, deterministic seed, snapshot keys match
  `listings.example.json`, every documented trap present in code and README), wired into CI on both OSes.
- `tests/smoke.py` grew from 141 to 149 assertions.

## 0.3.0 — 2026-11-04

The audit stopped being a paragraph and became a check that can fail a batch — and so did the verification
before each submit.

- `scripts/reconcile.py`: compares the plan against a snapshot of the shop on **name + price + spec
  structure**, and prints MISSING / DUPLICATE / PRICE / STRUCTURE plus a status list for rows that exist but
  are not live. Expects either `queue.json` or the registration workbook (a sheet carries no spec names, so
  the structure dimension is announced as skipped rather than silently passing). Exit 0 clean, 1 differences,
  2 unusable input; `--out diff.json` for machine-readable results.
- `listings.example.json`: the snapshot shape the agent writes after scraping the list, with the platform's
  own row count in `total` — reconcile refuses a snapshot whose rows do not match it, because a missed page
  reads as missing products and a fake missing product is how a duplicate listing gets created.
- Name normalisation folds full-width punctuation and case-pack suffixes (`（箱*24瓶）`, `(1*24罐)`, `*24罐`)
  so one product written four ways reconciles once, while a parenthesised size (`(2000g)`) stays part of the
  name so 盐焗鹅 and 卤鹅 are not merged.
- Same name + different price is never a duplicate; same name + same price twice is.
- Unrecognised status wording is reported, not guessed into a bucket; `state.json` is documented as a work log,
  never evidence that a listing exists.
- `make_queue.py --board pickup` tags items with the shop section, so self-pickup and courier plans are
  audited apart and one never satisfies the other.
- `reconcile.py` reads files only: no network module in the source, asserted in CI.
- References: browser playbook gained a list-snapshot section; the catalogue gained three entries (a missed
  page faking missing rows, a pending-review row relisted, and a local log believed over the shop).
- `scripts/verify_item.py`: the pre-submit gate. The agent reads the filled form back into `actual.json` and
  the script compares it against the queue item and the profile, printing one PASS/FAIL/SKIP line per rule -
  name (including the form's own length limit, checked before anyone types), the **exact** category path
  (a parent node whose label contains the leaf fails), scope, spec name, spec-value order (unit row before
  case row, because rows can only be appended), SKU row count, price/stock/points per row, display counters,
  every required upload slot against `image.minBytes` using the *stored* byte size, the pre-checked control
  count, and the agreed submit mode. Exit 1 blocks the submit; `fill-only` with a submitted form fails as
  `OUT-OF-MODE`. Verification written fresh each round drifts with attention - this makes rule sixteen the same
  on product one and product one hundred and twenty.
- `actual.example.json`: the read-back shape, including that SKU rows may be dumped as arrays and are then
  keyed by `fields.spec.skuFieldOrder`.
- Hardened against a cross-review of the first draft, each fix pinned by its own test: the gate no longer
  blesses what it never compared (an undeclared extra spec row was passing - declare variants in the item's
  `specValues` or pass `--allow-extra-specs`, which still prints which rows went unchecked); the category path
  is matched segment by segment, so a breadcrumb rendered with `>` is not a false block while a missing level
  stays a failure; an explicit `image.minBytes` of 0 means no floor instead of silently becoming 50 KB; a
  non-UTF-8 input is an input error (exit 2) rather than a traceback that reads like a failed check; and a
  `--out` that cannot be written warns instead of overwriting the verdict's exit code.
- `SKILL.md` Phase 6 runs the gate instead of the sentence "verify every field"; the playbook gained a
  read-back section and the catalogue two entries (strictness drifting mid-batch, submitting in fill-only mode).
- Evals: eleven briefs.

## 0.2.0 — 2026-11-04

The probe result became the single source of truth: the queue now reads it, and the scripts stopped
promising things they did not do.

**Breaking** (0.x, and all three changes are the point of the release):

- `make_queue.py` requires `--profile profile.json`. Root category, path separator, case-row label
  template, points policy, coupon floor, display counters, stock and spec name come from the profile;
  `--root` / `--min-coupon` / `--display-counters` / `--no-case-sku` / `--stock` / `--spec-name` remain as
  per-run overrides. `--points-as-coupon` became `--points none|coupon|fixed`.
- `crop_square.py --tops` became `--windows "WxH:top[:bottom]"` — a bottom bound covers photo areas shorter
  than the image width (the phone-screenshot case), padded white and centred.
- `check_images.py --glob "*.jpg"` became `--ext jpg,jpeg,png,webp` and walks subfolders, because a crops
  folder that mixes extensions used to be audited half-blind.

Added / fixed:

- `profile.json` models every upload slot (`image.trays[]`: `main` / `detail` / per-SKU cell), with its own
  crop behaviour (`square` / `any` / `none`), `maxCount` and `bind`. `check_profile.py` validates trays,
  requires the path separator, the SKU numeric order and a case-row label whenever `business.caseSku` is on.
- `crop_square.py` honours the `alt_src` fallback its own docstring promised, and a crop judged blank is no
  longer written to disk at all.
- `make_queue.py` validates each product before use (no stack traces), drops same name + same retail,
  reports same name + different price instead of hiding it, and aborts without writing a queue when a value
  is missing, a category key is unknown or a coupon is impossible.
- `fill_xlsx.py` finds the first empty row from the name column (`max_row` lies on sheets that end in styled
  blanks), writes rows that have no supply price, inherits the previous row's border, forces black text over
  leftover red template font, flags the workbook for recalc on open, and refuses to overwrite filled rows.
- `SKILL.md` documents the `state.json` resume record that Phase 6 and the evals had been referring to.
- Every script now reconfigures stdout/stderr to UTF-8: a Windows console opens on cp1252/cp936 and died with
  `UnicodeEncodeError` halfway through printing a Chinese product name. The first Windows CI run caught this.
- References: tray inventory in the probe checklist (now 11 steps), tray-driven upload loop, plus catalogue
  entries for blank crops written to disk, extensions lying about format, a submit that silently does
  nothing, reviewed listings that refuse to save, and a queue drifting from the profile.
- The repository was renamed upstream: install command, plugin manifests and the git remote now all point at
  `https://github.com/TheChosenOne666/Auto-ListProducts-skill`.
- Evals: nine briefs, adding a second-platform run and a no-supply sheet row.
- `tests/smoke.py`: builds screenshots, a profile and a workbook, runs all five scripts and asserts on the
  artifacts. CI runs the whole suite on Linux **and** Windows.

## 0.1.0 — 2026-11-01

First public version.

- Discovery-first workflow: preflight → probe → profile → pilot → queue → fill → audit.
- `scripts/check_profile.py` refuses a half-probed profile before any product is touched.
- `scripts/crop_square.py` crops square images and guards two real failures: blank crops (the source
  screenshot's product photo never rendered) and carousel dots leaking into the bottom edge.
- `scripts/check_images.py` audits local files or hosted URLs for blank uploads.
- `scripts/make_queue.py` builds a resumable queue; points, display counters, a coupon floor and the case
  SKU row are opt-in, so no business rule is baked in.
- `scripts/fill_xlsx.py` appends registration rows while keeping price columns numeric and formulas alive.
- References: probe checklist, browser playbook, failure catalogue.
- Evals: seven briefs covering the preflight, probe, blank crop, dot artifact, session expiry, bundle pricing,
  missing browser channel and duplicate screenshots.
