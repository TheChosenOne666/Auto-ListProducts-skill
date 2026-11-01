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
1. **Probe.** Open the create form, read the required fields and one stable locator each, dump the category
   tree level by level, note pre-checked defaults, and find the verification hooks (stored image URL,
   points echo, confirm dialog, post-submit route). Ask the user: default scope, stock, spec name, root
   category, whether points exist and how they derive, display counters, case SKU, skip rule, submit mode.
   Write `profile.json` and validate it with `scripts/check_profile.py`.
2. **Pilot.** Fill one product end-to-end with the profile. Only after it passes every assertion, scale.
3. **Crop.** `scripts/crop_square.py` turns screenshots into square images and refuses two failure modes
   it has seen in the wild: a crop of a product photo that never rendered (blank), and the carousel dots
   that leak into the bottom edge (capped off and white-padded back to square).
4. **Extract.** Record title, size/unit, units per case, retail, case supply, image, category key. Unreadable
   values are asked, never guessed. Arithmetic is sanity-checked: a bundle is one unit, its cost is
   single-pack cost × packs.
5. **Sheet (optional).** `scripts/fill_xlsx.py` appends rows without breaking formulas — the supply price
   stays numeric and the case pack is shown with `number_format`.
6. **Queue.** `scripts/make_queue.py` builds resumable jobs. Points, display counters, a coupon floor and
   the case SKU row are all opt-in; there is no built-in business rule.
7. **Fill loop.** For each job: fresh form, upload both images and verify the stored bytes, fill name →
   category → scope → spec values → SKU rows, verify every field against the queue, submit only in
   `fill-and-submit` mode, assert the route changed, persist progress.
8. **Audit.** Rescan the list for duplicates and gaps, spot-check hosted images, report.

## Contents

```
SKILL.md                        the run, the preflight, the fill loop, the gates
profile.example.json            what the probe produces
references/probe-checklist.md   the 10-step discovery procedure behind the probe
references/browser-playbook.md  uploads, real input events, tab handoff, window hygiene
references/pitfalls.md          failure catalogue: symptom → cause → fix
scripts/check_profile.py        refuse a half-probed profile before touching products
scripts/crop_square.py          square crops + blank and carousel-dot guards + contact sheet
scripts/check_images.py         blank-image audit for local files or hosted URLs
scripts/make_queue.py           facts → queue (optional points / counters / skip rule)
scripts/fill_xlsx.py            append rows without breaking formulas
evals/evals.json                test briefs with expected behaviour
```

## Requirements

- Python 3.10+ with `pillow` and `openpyxl` (`pip install pillow openpyxl`).
- A browser the agent can control (a connector/extension bridge to the user's own browser), with real
  input events available for native file dialogs.
- No API keys. No installs beyond the two Python packages.

## Install

```bash
npx skills add https://github.com/TheChosenOne666/Auto-ListProducts --skill "auto-list-products"
```

Or copy `skills/auto-list-products/` into your agent's skill directory
(`~/.qoder-cn/skills/`, `~/.claude/skills/`, or a project's `.qoder/skills/`).

## Safety

Fill forms freely; submit only when the user authorised it. Never type credentials — hand the tab back on a
login page. Never bypass uploads, review, rate limits or anti-bot checks with page scripts.
