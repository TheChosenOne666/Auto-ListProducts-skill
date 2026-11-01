# Probe checklist — turn a live admin form into `profile.json`

One probe per backend. Budget 5–10 minutes with the user present, because the business questions in
step 8 cannot be answered by the page.

Work on the **create** form (the empty one), not on an existing product. If the platform autosaves drafts,
note that and close the form without saving when you finish probing.

## 1. Routes

- open the product list, click "new/create", record the URL — that is `form.newUrl` (no id parameter).
- record the list URL as `form.listUrl`.
- log out is not something you test; just note the login route fragment you saw, e.g. `/login`, as
  `form.loginUrlPattern`.

## 2. Field inventory

Read the form through the accessibility tree / labels, top to bottom. For each field record:

- visible label and whether it is marked required,
- the element kind: text input, cascader, select, number stepper, checkbox/switch, rich text, upload tray,
- **one stable locator**: placeholder text, label text, role+name, or a css class as a last resort,
- the current default value, if any.

Skip nothing because it "looks optional" — hidden required fields are the usual cause of a submit that
silently refuses.

## 3. Category tree

- open the cascader; read the labels **per panel**, one level at a time (a flat read mixes levels),
- note the separator used when a selection reads back (e.g. `A / B / C`),
- note whether a level allows free text or only leaves,
- record how a leaf is confirmed (click closes the panel / explicit confirm button).

## 4. Spec matrix

- type a spec name, add one value, then a second value; confirm SKU rows appear in value order,
- record the locator for the numeric SKU inputs and the **order** of the numbers in a row
  (price, stock, points, barcode, listed toggle …),
- note the label format used for the case row (so the queue can generate it),
- note whether a bulk-fill widget exists and when it is unsafe (it overwrites all rows).

## 5. Defaults and pre-checked controls

List every control that is already in the desired state on a fresh form (payment types, scope select,
banner toggle, commission inheritance, auto-generated codes). The fill loop will **assert** these instead
of clicking them, so a future UI change is caught.

## 6. Verification hooks

Upload one image and inspect it:

- read the thumbnail's `src` and fetch it — record its byte size; a blank crop is ~10 KB,
- find where the stored image lives in the form (thumbnail css),
- find where points/price echo back (preview text),
- note the exact confirmation dialog text after clicking submit,
- note what changes on success (route leaves the form / toast / row appears in the list).

## 7. Submit path

Do the pilot submit (only if the user authorised `fill-and-submit`; otherwise stop before submit and ask).
Record the success condition you actually observed.

## 8. Ask the user (the page cannot answer these)

- which scope/region option should be used by default,
- stock number, spec name label, root category for their catalogue,
- do points exist? derived from coupon, a fixed number, or not at all?
- fill display sales/favourite counters? with what policy?
- one SKU row (single unit) or two (single + case)?
- any minimum coupon / exclusion rule? (default: none)
- fill-only or fill-and-submit.

## 9. Write and validate

Write `profile.json` (shape: `profile.example.json`), then:

```bash
python scripts/check_profile.py profile.json
```

Fix every `ERR` before proceeding. `WARN` lines are defaults you may override.

## 10. Pilot one product

Fill a single product end-to-end with the profile. Only after that pilot passes every assertion, build the
queue and batch. Keep the first three batch items at full verification strength.
