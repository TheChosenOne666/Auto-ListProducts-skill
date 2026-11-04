# auto-list-products

A skill for bulk-listing products on **any** web admin backend. The agent probes the user's own create
form once, writes a platform profile, has the user answer the business questions the page cannot answer,
and then runs a resumable queue: crop, extract, fill, verify, submit, audit.

Nothing about a platform is hardcoded — the profile is generated from the live form, so a new backend
costs one guided probe, not a code change.

## How it works

0. **Preflight.** Check the browser-control channel (if the connector/extension is missing, stop and help
   the user install and connect it), confirm real input events are possible, check Python deps, confirm
   the session is logged in, and agree `fill-only` vs `fill-and-submit`.
1. **Probe.** Open the create form, read the required fields and one stable locator each, inventory every
   upload tray (product image, detail images, per-spec-row cell — each with its own crop behaviour), dump
   the category tree level by level, note pre-checked defaults, and find the verification hooks (stored image
   URL, points echo, confirm dialog, post-submit route). Ask the user: default scope, stock, spec name, root
   category, whether points exist and how they derive, display counters, case SKU and how that row is
   labelled, skip rule, submit mode. Write `profile.json` and validate it with `scripts/check_profile.py`.
2. **Pilot.** Fill one product end-to-end with the profile. Only after it passes every assertion, scale.
3. **Crop.** `scripts/crop_square.py` turns screenshots into square images and refuses two failure modes it
   has seen in the wild: a crop of a product photo that never rendered (blank — never written, retried from
   the `alt_src` screenshot first) and the carousel dots that leak into the bottom edge (capped off and
   white-padded back to square). Per-resolution bounds accept an optional bottom edge for photo areas that
   are shorter than the image width.
4. **Extract.** Record title, size/unit, units per case, retail, case supply, image, category key. Unreadable
   values are asked, never guessed. Arithmetic is sanity-checked: a bundle is one unit, its cost is
   single-pack cost × packs.
5. **Sheet (optional).** `scripts/fill_xlsx.py` appends rows without breaking formulas — it finds the first
   empty row itself, keeps the supply price numeric (case pack via `number_format`), writes a literal coupon
   for rows that have no supply price, inherits the previous row's border and forces black text.
6. **Queue.** `scripts/make_queue.py --profile profile.json` builds resumable jobs from the profile: root
   category, path separator, case-row label template, points policy, coupon floor, display counters, stock
   and spec name. Same name + same retail is dropped as a duplicate; a missing value, unknown category key or
   impossible coupon aborts without writing a queue. Flags override the profile for one run only.
7. **Fill loop.** For each job: fresh form, upload each tray the profile declares, fill name → category →
   scope → spec values → SKU rows, then read the form back into `actual.json` and run `verify_item.py` — one
   PASS/FAIL/SKIP line per rule (category path exact, spec order, SKU numbers, stored image bytes, pre-checked
   count, submit mode). Exit 1 blocks the submit. Only in `fill-and-submit` mode does it then submit, accept the
   confirmation and assert the route changed; progress persists in `state.json`.
8. **Audit.** Scrape the product list into a snapshot (`listings.json`, with the platform's own row count in
   `total`), then `scripts/reconcile.py` compares plan against shop on name + price + spec structure and prints
   four lists — MISSING / DUPLICATE / PRICE / STRUCTURE — plus a status list for rows that exist but are not
   live, and a count of snapshot rows outside the plan. Exit 1 means the batch is not finished.

## Contents

```
SKILL.md                        the run, the preflight, the fill loop, the gates
profile.example.json            what the probe produces (trays, locators, defaults, business policy)
actual.example.json             what to read off the filled form before the submit gate
listings.example.json           what the audit reads back from the product list
references/probe-checklist.md   the 11-step discovery procedure behind the probe
references/browser-playbook.md  uploads, real input events, tab handoff, read-back, snapshot scraping
references/pitfalls.md          failure catalogue: symptom → cause → fix
scripts/check_profile.py        refuse a half-probed profile before touching products
scripts/crop_square.py          square crops + blank and carousel-dot guards + contact sheet
scripts/check_images.py         blank-image audit for local files or hosted URLs
scripts/make_queue.py           facts + profile → queue
scripts/fill_xlsx.py            append rows without breaking formulas
scripts/verify_item.py          the pre-submit gate: per-rule PASS/FAIL/SKIP, exit 1 blocks the submit
scripts/reconcile.py            plan vs shop: missing / duplicate / price / structure / status
evals/evals.json                test briefs with expected behaviour
```

The repo also runs `tests/smoke.py` in CI on both Linux and Windows: it builds screenshots, a profile, a
workbook and a shop snapshot, runs every script and asserts on the artifacts, so a script that compiles but
misbehaves fails the build on the platform the operator actually uses.

## Requirements

- Python 3.10+ with `pillow` and `openpyxl` (`pip install pillow openpyxl`).
- A browser the agent can control (a connector/extension bridge to the user's own browser), with real
  input events available for native file dialogs.
- No API keys. No installs beyond the two Python packages.

## Install

```bash
npx skills add https://github.com/TheChosenOne666/Auto-ListProducts-skill --skill "auto-list-products"
```

Or copy `skills/auto-list-products/` into your agent's skill directory
(`~/.qoder-cn/skills/`, `~/.claude/skills/`, or a project's `.qoder/skills/`).

## Safety

Fill forms freely; submit only when the user authorised it. Never type credentials — hand the tab back on a
login page. Never bypass uploads, review, rate limits or anti-bot checks with page scripts.
