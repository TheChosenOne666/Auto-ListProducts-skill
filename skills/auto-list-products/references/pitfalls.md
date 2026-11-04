# Failure catalogue

Each entry: symptom → cause → fix. All of these have bitten a real batch.

## Blank product image submitted

Symptom: the thumbnail is an empty white box; the stored image is ~10 KB with a dozen colours.
Cause: the source screenshot's product photo never loaded, so the crop captured pure white — or the crop
dialog was confirmed before the image rendered.
Fix: run `check_images.py` on the crops before uploading; treat `<50 KB` as blank; re-crop from a
duplicate screenshot of the same product (`alt_src`), or ask the user to re-send the screenshot.

## Carousel dots inside the product image

Symptom: three small dots at the bottom edge of an otherwise clean square.
Cause: the fixed square window is taller than the photo area in that screenshot.
Fix: `crop_square.py` detects the gap-then-dots pattern, caps the crop above it and pads white back to a
square. Verify on the contact sheet; do not simply zoom the platform cropper (fixed crop boxes often have
no usable pan/zoom controls).

## Blank crop saved next to the good ones

Symptom: the crops folder looks complete, and one file is a white square.
Cause: the crop step wrote the blank result anyway, so the batch uploaded it.
Fix: `crop_square.py` writes only crops that clear the colour test; a blank entry either resolves through
`alt_src` or exits 2 with the file left unwritten. `check_images.py` is the second net, on the stored bytes.

## Extension lies about format

Symptom: an audit reports "no images" or a decoder rejects a file named `.jpg`.
Cause: backends and phones re-encode on save, so the name and the content disagree; a folder scanned with a
`*.jpg` pattern silently skips the PNG crops.
Fix: scan by content and by a list of extensions (`check_images.py --ext jpg,jpeg,png,webp`) and judge the
fetched bytes, not the file name.

## Duplicate listings

Symptom: the same product appears twice.
Cause: repeated screenshots within a batch, or a deleted-and-relisted item whose delete did not commit.
Fix: dedupe by title + spec before building the queue (`make_queue.py` drops same name + same retail and
reports it); after any delete-and-relist, rescan the product list and count occurrences per name (must be
exactly one).

## Same name, different price

Symptom: the queue reports two products sharing a name but keeps both.
Cause: that is often legitimate — the same item in different pack sizes or grades, and name-only matching
hides it.
Fix: keep both, confirm with the user, and reconcile inventories by name **plus** price and SKU structure.
Matching on name alone makes an existing listing look like a duplicate and get skipped.

## Submit button does nothing

Symptom: the click lands, no confirmation dialog, no error text, the form just stays.
Cause: a required field you never saw — often one that belongs to a different shipping/section mode, or an
empty numeric column in the SKU table.
Fix: read the form's own validation output (per-field error text, a red label, a scrolled-to section); if
there is none, treat every field in the active section as a candidate and probe them one at a time. Record
what it was in `profile.notes` so the next batch does not rediscover it.

## Approved listing refuses to save

Symptom: on an item that already passed review, edits are accepted by the dialog but the record is
unchanged, and the URL never leaves the form.
Cause: some backends freeze a reviewed product and only allow a new listing from scratch.
Fix: back up the current field values and images, delete the listing, then create a fresh one. Expect the
status to fall back to "pending review", and confirm the old row really disappeared before relisting so the
batch does not end up with two copies.

## Batch dies halfway with UnicodeEncodeError

Symptom: a script that ran fine in a Linux or UTF-8 terminal crashes on a Windows console while printing a
product name or a report line, leaving a batch half-finished.
Cause: the console opens on a legacy codepage (cp936/cp1252), so writing the user's own Chinese labels raises
before the line reaches the screen - the data is fine, the stdout encoding is not.
Fix: the scripts reconfigure stdout/stderr to UTF-8 with replacement at startup, and `tests/smoke.py` asserts
it under a Chinese product name and case word. If you add a script to this skill, do the same or it will fail
on the first batch that carries real names.

## Queue disagrees with the profile

Symptom: the numbers filled into the form are not what the user agreed to; the flags were re-typed per batch.
Cause: business rules were passed on the command line instead of read from the profile, so the two drifted.
Fix: always build with `make_queue.py --profile profile.json`; flags exist to override one run, and the
printed `profile:` line states which values were used.

## Coupon / margin nonsense

Symptom: coupon is negative, or above 60% of the retail price.
Cause: a bundle (3-pack, 6-pack, case) priced as if it were a single pack, or a per-bottle supply price
used as a per-case price.
Fix: identify the selling unit first. A bundle's cost = single-pack cost × packs per bundle; a case's
unit cost = case price / units per case. Rework the numbers with the user before listing.

## Spreadsheet #VALUE!

Symptom: coupon and unit-cost columns turn into errors after a paste.
Cause: the supply-price column was written as text (e.g. `86（箱*24瓶）`).
Fix: keep the cell numeric and apply `number_format` `0.##"（箱*24瓶）"` instead. `fill_xlsx.py` does this.

## Formulas show 0 or stay blank

Cause: the workbook was written programmatically, so formulas have no cached value.
Fix: tell the user to reopen the file or press F9.

## Workbook refuses to save

Cause: Excel holds the file (`~$name.xlsx` lock).
Fix: ask the user to close it; back up before writing.

## Upload dialog never appears

Cause: the file-chooser interceptor from an earlier `setFiles` attempt is stuck; synthetic clicks cannot
dismiss it.
Fix: reset the browser agent, recreate it, re-claim (or open) the tab, then click the upload tile with a
real input event.

## No browser-control channel at all

Symptom: the agent cannot list or open the user's tabs, or the connector reports itself disconnected.
Cause: the browser-connector extension is not installed / not enabled / outdated, or the agent is pointed
at a different browser surface.
Fix: stop the batch. Walk the user through installing and connecting the extension, then re-check. Never
continue in an embedded browser that merely shows the same site - it is a separate, logged-out session.

## Uploader has no usable file input

Symptom: clicking the upload tile never opens the OS dialog, and there is no `input[type=file]` to set.
Cause: the component creates a detached input or relies on a trusted click that the environment cannot
produce.
Fix: use a real OS-level click (element index / mouse) to open the native dialog; if the environment has no
OS input at all, stop and say so. Do not fake an upload by scripting the page.

## Session expired mid-batch

Symptom: submissions start failing; the tab shows a login route.
Fix: hand the tab to the user for login, wait for confirmation, re-claim, and resume from
`state.json` — never replay an unverified submit.

## "Success" toast but nothing saved

Cause: the confirmation dialog was dismissed, or validation failed silently.
Fix: assert the route left the form page; if it did not, capture the form error text and stop.

## Category landed on the wrong leaf

Cause: contains-matching a label (`Tea` inside `Tea (brand direct)`), or reading all panels as one list.
Fix: exact trimmed match, panel-scoped reads, and assert the joined path afterwards.

## Tab closed with the form inside

Cause: agent-created tab auto-closed at turn end.
Fix: work in the user's tab and mark it as handoff before pausing.
