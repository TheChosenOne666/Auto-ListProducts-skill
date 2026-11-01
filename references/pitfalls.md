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

## Duplicate listings

Symptom: the same product appears twice.
Cause: repeated screenshots within a batch, or a deleted-and-relisted item whose delete did not commit.
Fix: dedupe by title + spec before building the queue; after any delete-and-relist, rescan the product
list and count occurrences per name (must be exactly one).

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
