# Changelog

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
