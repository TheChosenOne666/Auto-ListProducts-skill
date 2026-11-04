# Browser playbook

Hard-won mechanics for driving an admin backend through a browser-automation agent.
Selectors below are Element-Plus-style examples; the real names come from `profile.json` written by the probe.

## Before the first click: is the channel actually available?

1. Ask the agent for its browser list and open a tab. An empty list, or a "connector not ready /
   extension missing / update required" style error, means the user has **no working browser-control
   bridge** — stop and fix that first:
   - tell the user which browser connector extension (or companion app) to install,
   - have them enable it in the browser and connect it,
   - reopen the browser, then re-run the check.
   Do not continue against any other surface. A separate in-app/embedded browser that happens to show the
   same site is a **different session**: not logged in, different cookies, wasted batch.
2. Prove control end-to-end with a harmless action (list tabs, read the page title, open the admin URL) and
   confirm the page is the logged-in admin, not a login form.
3. Confirm OS-level input is available (needed for native file dialogs). Without it, only proceed if the
   uploader exposes a plain file input that accepts files directly.
4. Agree the mode for this run: fill-only, or fill-and-submit.

## Ownership and tab hygiene

- Work in **one tab**, the user's own tab. List the user's tabs, claim the store tab, and re-claim it at
  the start of every round — claim tokens are one-use and expire between turns.
- Open a **fresh form URL** for every product. Never continue in a half-filled form.
- Before ending a round with a pending review, mark the tab as handoff. Agent-created tabs auto-close at
  turn end; an unmarked tab will take the filled form with it.
- Avoid creating new tabs per round. Do not repeatedly activate the window: activation/snapshot calls can
  unmaximize or minimize it. Snapshot without activating when possible; restore size once at the end
  (`Alt+Space`, then `x`).

## Uploads: real clicks only

Walk `image.trays` in profile order. A tray bound to the product is filled once per listing; a tray bound
to `sku` is filled once per spec row (that is where per-variant photos go). `crop: any` trays take the file
as-is and may not open a dialog at all; `crop: square` trays always need the confirm step.

Synthetic clicks (dispatched through the debugger) are captured by the file-chooser interceptor, the
native dialog never appears, and `setFiles` is refused. The working recipe:

1. Snapshot the browser window with accessibility text.
2. Find the upload tile by its button label (`批量上传`, `更换图片`, …) in the tree; note its element index.
3. Click it **by element index** (a real input event).
4. Wait for the OS "Open" window, then read its focused element.
5. `set_value` the filename edit with the exact file name (Chinese is fine; avoid typing — characters drop),
   then press `Return`.
6. Wait for the crop dialog, wait for the image to actually render, then click the confirm button
   (`裁剪并上传`).

After the upload completes, verify the stored object: read the thumbnail's `src` and fetch it.
A white crop compresses to ~10 KB; anything under `image.minBytes` (50 KB by default) means the crop was
blank — delete the thumbnail (`removeLocator`) and retry, at most twice, then abort the item. Extensions lie
about format: a file named `.jpg` can be a PNG, so judge the stored bytes, never the name.

If no OS dialog ever appears, the chooser interceptor is stuck: reset the browser agent, recreate it, and
re-claim (or reopen) the tab.

Do not expect to fix a bad crop in the page: many crop dialogs expose a fixed box with no pan or zoom, and
dragging it closes the dialog. Re-crop locally instead.

## Category cascader

- Click the field once to open; if it is already open, clicking again closes it — check visibility first.
- Read labels **per panel** (`.el-cascader-menu` nth), never as one flat list: level-1 and level-2 share
  many strings and a flat search will click the wrong node.
- Click by **exact trimmed text** and assert the resulting path string afterwards.
  A contains-match can silently land on a sibling (`Tea (brand direct)` vs `Tea`).
- Category names are data, not guesses: dump the tree per level, store it in `categories.json`, and
  re-dump whenever the platform adds nodes.

## Selects and toggles

- Click the select, then click the option by exact text; retry the whole open→pick cycle up to three
  times and assert the rendered value before moving on.
- Defaults (payment types, banner checkbox, commission) are usually pre-checked; assert the count
  instead of clicking them.

## Spec matrix and SKU rows

- One spec name, several values: type the first value, click `+增加规格值`, type the next. Keep the order the
  queue gives you (single unit first, case row after) — a row added later can only be appended, so a wrong
  order means deleting and re-typing the values.
- SKU rows appear in value order. Fill the numeric inputs by index using `fields.spec.skuFieldOrder` (price,
  stock, points, …) and read all of them back before submitting; do not use the bulk-fill widget when rows
  differ.
- Editing an existing product's spec values resets the numbers on those rows; re-fill and re-read them.

## Submit and resume

- Click submit, accept the confirmation dialog, then assert the route left the form page.
- Persist the queue index after each success (`state.json`) so a crash resumes cleanly.
- Stop the batch on the first failed assertion and report the item; never "fix it by clicking anyway".

## List snapshot

The audit compares against a snapshot of the product list, not against memory of what was submitted.

- Walk **every page** and count the rows. Put the platform's own count into `total`; `reconcile.py` refuses a
  snapshot whose `total` disagrees with its rows, because a missed page reads as a set of missing products and
  a fake missing product is how a duplicate listing gets created.
- Record per row: the id, the name exactly as displayed, the status wording exactly as displayed (do not
  translate it into a guess - an unmapped word is reported instead of being silently bucketed), and one entry
  per spec row with its price and points.
- Two sections in the shop are two snapshots: give each file a `board`, or pass `--board` per file. Never
  compare a self-pickup plan against a courier snapshot.
- Write the file into the run folder with the batch timestamp in its name; `--out diff.json` overwrites, and
  the previous diff is the only record of what the state was before the fix.
- Filter menus matter: a list opened with a status filter applied yields a snapshot that cannot see the rows
  in other states. Clear the filters first.

## Session and safety

- Session expiry shows up as a redirect to a login route. Hand the tab to the user, wait for their word,
  then re-claim and continue. Never type credentials.
- Do not tamper with the page to bypass uploads, review, or anti-bot checks.
