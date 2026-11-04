# Changelog

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
