#!/usr/bin/env python3
"""Compare two probe dumps, and check a profile against what was actually observed.

`profile.json` records what we decided to do with a backend. `probe-dump.json` records what the form
looked like when it was probed - the category tree read panel by panel, every field with its marked
requiredness and its measured behaviour, the upload slots, the SKU column order, the submit wording.
Only the second one can answer "did the platform change?", and only the pair can answer
"did we copy it wrong?".

The agent writes the dump by observing the form; this script never fabricates one. It compares two of
them, or checks a profile against one, and says what each difference would break.

Usage
  python diff_probe.py probe-2026-11-04.json --check-profile profile.json   # did we copy it wrong?
  python diff_probe.py probe-2026-10-31.json probe-2026-11-04.json           # did the platform change?
  python diff_probe.py old.json new.json --check-profile profile.json --out drift.json
  python diff_probe.py pickup.json express.json --cross-board   # section difference, not drift
  options: --verbose  (also print how many things did not change)

Exit codes: 0 nothing changed and the profile is consistent, 1 drift or inconsistency (a human decides),
2 unusable input - including two dumps from different boards compared without --cross-board, and two
dumps of the same capture compared with each other (that pair cannot answer "did the platform change").
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REQUIRED_SECTIONS = ("categoryTree", "fields", "trays", "skuColumns")
SECRET_TOKENS = ("password", "token", "cookie", "secret", "authorization")


class InputError(Exception):
    """The dumps are not comparable as written."""


def load_json(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"no such file: {path}")
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON: {exc}")
    except UnicodeDecodeError as exc:
        raise InputError(f"{path} is not UTF-8 text ({exc}); save the dump as UTF-8")
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc}")


def reject_secrets(doc, path: str):
    """A probe dump is an observation, not a credential store."""
    if isinstance(doc, dict):
        for key, value in doc.items():
            if any(token in str(key).lower() for token in SECRET_TOKENS):
                raise InputError(f"{path}: key {key!r} looks like a credential; keep those out of dumps")
            reject_secrets(value, path)
    elif isinstance(doc, list):
        for item in doc:
            reject_secrets(item, path)


def load_dump(path: str) -> dict:
    doc = load_json(path)
    if not isinstance(doc, dict):
        raise InputError(f"{path}: a dump must be an object")
    missing = [section for section in REQUIRED_SECTIONS if section not in doc]
    if missing:
        raise InputError(f"{path}: missing sections {missing} (see probe-dump.example.json)")
    if not isinstance(doc["categoryTree"], dict) or not isinstance(doc["fields"], list) \
            or not isinstance(doc["trays"], list) or not isinstance(doc["skuColumns"], list):
        raise InputError(f"{path}: categoryTree must be an object; fields, trays and skuColumns must be lists")
    reject_secrets(doc, path)
    check_shape(doc, path)
    return doc


def check_shape(doc: dict, path: str) -> None:
    """A malformed member must cost the run its exit code 2, not a traceback that reads as exit 1."""
    for item in doc["fields"]:
        if not isinstance(item, dict) or item.get("label") in (None, ""):
            raise InputError(f"{path}: every field entry needs a label; got {item!r}")
    for item in doc["trays"]:
        if not isinstance(item, dict) or not item.get("key"):
            raise InputError(f"{path}: every tray entry needs a key; got {item!r}")
    keys = [str(item.get("key")) for item in doc["trays"]]
    if len(keys) != len(set(keys)):
        raise InputError(f"{path}: duplicate tray keys {sorted(k for k in set(keys) if keys.count(k) > 1)}; "
                         "two slots with one key cannot be told apart by any of the scripts")
    for name, column in enumerate(doc["skuColumns"]):
        if not isinstance(column, str):
            raise InputError(f"{path}: skuColumns[{name}] must be the column label as the page shows it, "
                             f"got {column!r}")
    for name, note in enumerate(doc.get("observations") or []):
        if not isinstance(note, str):
            raise InputError(f"{path}: observations[{name}] must be a sentence; a bare string here would be "
                             "split into characters and reported as many notes")
    if isinstance(doc.get("observations"), str):
        raise InputError(f"{path}: observations must be a list of sentences, not one string")
    check_tree(doc["categoryTree"], path)
    if "submit" in doc and not isinstance(doc["submit"], dict):
        raise InputError(f"{path}: submit must be an object")
    if "separator" in doc and not isinstance(doc["separator"], str):
        raise InputError(f"{path}: separator must be a string")


def check_tree(tree, path: str, depth=0) -> None:
    if depth > 12:
        raise InputError(f"{path}: categoryTree nests too deep to be a category tree")
    for label, child in (tree or {}).items():
        if not isinstance(label, str):
            raise InputError(f"{path}: categoryTree labels must be the text on the page, got {label!r}")
        if child is None:
            continue
        if not isinstance(child, dict):
            raise InputError(f"{path}: categoryTree['{label}'] must be a node or null, got {child!r}")
        check_tree(child, path, depth + 1)


def flatten(tree: dict, prefix=()) -> set:
    paths = set()
    for label, child in (tree or {}).items():
        here = prefix + (label,)
        paths.add(here)
        if isinstance(child, dict):
            paths |= flatten(child, here)
    return paths


def field_key(field: dict) -> str:
    return f"{field.get('label')}@{field.get('in', 'form')}"


def field_name(field: dict) -> str:
    """How a field is shown: bare label on the form, with its location inside a SKU row."""
    where = field.get("in", "form")
    return str(field.get("label")) if where == "form" else f"{field.get('label')}@{where}"


def field_index(fields) -> dict:
    """`label@in` keyed, occurrences numbered: a form with two controls under one label is real,
    and the second one must not silently replace the first."""
    out, seen = {}, {}
    for item in fields or []:
        if not isinstance(item, dict):
            continue
        base = field_key(item)
        seen[base] = seen.get(base, 0) + 1
        out[f"{base}#{seen[base]}"] = item
    return out


def duplicated_bases(before: dict, after: dict) -> set:
    bases = {}
    for key in set(before) | set(after):
        base = key.rpartition("#")[0]
        bases[base] = bases.get(base, 0) + 1
    return {base for base, count in bases.items() if count > 1}


def display_base(base: str) -> str:
    """`label@in` → how a human reads it: bare label on the form, with its location inside a SKU row."""
    label, _, where = base.partition("@")
    return label if where in ("", "form") else f"{label}@{where}"


def shown_field(key: str, duplicated: set) -> str:
    """Only fields that actually repeat across the pair get their occurrence shown."""
    base, _, occurrence = key.rpartition("#") if "#" in key else (key, "", "")
    if base not in duplicated or not occurrence:
        return display_base(base)
    return f"{display_base(base)}#{occurrence}"


def index_by(items, key):
    out = {}
    for item in items:
        if isinstance(item, dict):
            out[key(item)] = item
    return out


# 平台的每个字段属性都会改；列出有明确后果的那些，剩下的走兜底比较——
# "这里没写到"永远不等于"变了但不报"。
FIELD_ATTR_NOTES = (
    ("required", "collection rules change; re-read the whole form, the asterisk is not the truth"),
    ("emptyBlocksSubmit", "a field you may now leave blank (or must not)"),
    ("default", "assertions on defaults will now fail - the page changed, not the plan"),
    ("options", "a profile option value may no longer exist on the form"),
    ("maxLength", "the name length the queue cuts at is keyed on this number"),
    ("kind", "the control type changed; how it gets filled and read back both change"),
    ("role", "the profile finds this field by role; a moved role breaks every lookup"),
    ("locator", "the recorded locator no longer matches the page"),
)


def diff_categories(old: dict, new: dict) -> list[str]:
    before, after = flatten(old.get("categoryTree")), flatten(new.get("categoryTree"))
    lines = []
    for path in sorted(after - before):
        lines.append(f"+ category {' / '.join(path)}  ->  a node nobody probed before; "
                     "if the catalogue needs it, the profile's tree and queues are missing it")
    for path in sorted(before - after):
        lines.append(f"- category {' / '.join(path)}  ->  every queue item routed here will fail the "
                     "category assertion and must be re-routed")
    return lines


def diff_fields(old: dict, new: dict) -> list[str]:
    before = field_index(old.get("fields"))
    after = field_index(new.get("fields"))
    repeated = duplicated_bases(before, after)
    lines = []
    for name in sorted(set(after) - set(before)):
        lines.append(f"+ field {shown_field(name, repeated)}  ->  a new control on the form; "
                     "the fill loop does not assert it yet")
    for name in sorted(set(before) - set(after)):
        lines.append(f"- field {shown_field(name, repeated)}  ->  locators pointing at it will find nothing")
    for name in sorted(set(before) & set(after)):
        a, b = before[name], after[name]
        shown = shown_field(name, repeated)
        for attr, note in FIELD_ATTR_NOTES:
            if attr == "required":
                changed = bool(a.get("required")) != bool(b.get("required"))
                value = f"required marker {a.get('required')} -> {b.get('required')}"
            elif attr == "emptyBlocksSubmit":
                changed = bool(a.get("emptyBlocksSubmit")) != bool(b.get("emptyBlocksSubmit"))
                value = f"empty-blocks-submit {a.get('emptyBlocksSubmit')} -> {b.get('emptyBlocksSubmit')}"
            else:
                changed = a.get(attr) != b.get(attr)
                value = f"{attr} {a.get(attr)!r} -> {b.get(attr)!r}"
            if changed:
                lines.append(f"~ field {shown} {value}  ->  {note}")
        known = {attr for attr, _ in FIELD_ATTR_NOTES} | {"label", "in"}
        for attr in sorted({*a, *b} - known):
            if a.get(attr) != b.get(attr):
                lines.append(f"~ field {shown} {attr} {a.get(attr)!r} -> {b.get(attr)!r}  ->  "
                             "something on this control moved; re-read the field before filling it")
    if repeated:
        lines.append(f"~ {len(repeated)} label(s) carry more than one control "
                     f"({', '.join(sorted(display_base(b) for b in repeated))}); they are compared in the "
                     "order the page lists them, so a re-order of two same-named fields reads as a value change")
    return lines


def diff_sku(old: dict, new: dict) -> list[str]:
    a, b = old.get("skuColumns") or [], new.get("skuColumns") or []
    lines = []
    for name in [c for c in b if c not in a]:
        lines.append(f"+ sku column {name}  ->  rows gain a field; index-based filling shifts")
    for name in [c for c in a if c not in b]:
        lines.append(f"- sku column {name}  ->  the profile's skuFieldOrder still counts it")
    if set(a) == set(b) and a != b:
        lines.append(f"~ sku column order {a} -> {b}  ->  the same set in a different order: every "
                     "index-based read or write is now off by one column")
    return lines


def diff_trays(old: dict, new: dict) -> list[str]:
    before = index_by(old.get("trays") or [], lambda t: str(t.get("key")))
    after = index_by(new.get("trays") or [], lambda t: str(t.get("key")))
    lines = []
    for key in sorted(set(after) - set(before)):
        lines.append(f"+ upload tray {key}  ->  a slot the plan does not fill")
    for key in sorted(set(before) - set(after)):
        lines.append(f"- upload tray {key}  ->  prepare_images and the fill loop still reference it")
    for key in sorted(set(before) & set(after)):
        a, b = before[key], after[key]
        for attr in ("crop", "maxCount", "accepts", "bind"):
            if a.get(attr) != b.get(attr):
                lines.append(f"~ tray {key} {attr} {a.get(attr)} -> {b.get(attr)}  ->  "
                             + ("slot layout and crop rules must follow" if attr in ("crop", "maxCount")
                                else "what you may upload here changed"))
    return lines


def diff_submit(old: dict, new: dict) -> list[str]:
    a, b = old.get("submit") or {}, new.get("submit") or {}
    lines = []
    for attr in ("button", "confirm", "success"):
        if a.get(attr) != b.get(attr):
            lines.append(f"~ submit {attr} {a.get(attr)!r} -> {b.get(attr)!r}  ->  "
                         "locators and the success assertion are keyed on this wording")
    return lines


def diff_observations(old: dict, new: dict) -> list[str]:
    before, after = set(old.get("observations") or []), set(new.get("observations") or [])
    lines = [f"+ observation {item}  ->  read it; it is the newest thing someone noticed"
             for item in sorted(after - before)]
    lines += [f"- observation {item}  ->  a note disappeared: expired or deleted by hand? "
              "the trap stays on the page when the record of it goes away"
              for item in sorted(before - after)]
    return lines


def board_view(profile: dict, board) -> dict:
    """这个板块的声明盖在整站声明之上；只有一板块的 profile 不受影响。

    板块覆盖只到 form/defaults/fields：槽位（image.trays）在 profile 里是整站共用的判断，
    check_profile.py 也不允许 boards.<key>.image，所以这里不合并它。
    """
    view = {"form": dict(profile.get("form") or {}),
            "defaults": dict(profile.get("defaults") or {}),
            "fields": dict(profile.get("fields") or {}),
            "without": []}
    over = (profile.get("boards") or {}).get(board) if board else None
    if not isinstance(over, dict):
        return view
    for section in ("form", "defaults"):
        view[section].update(over.get(section) or {})
    fields = over.get("fields") or {}
    view["without"] = [str(name) for name in (fields.get("without") or [])]
    for name, value in fields.items():
        if name == "without":
            continue
        if isinstance(value, dict) and isinstance(view["fields"].get(name), dict):
            merged = dict(view["fields"][name])
            merged.update(value)
            view["fields"][name] = merged
        else:
            view["fields"][name] = value
    for name in view["without"]:
        view["fields"].pop(name, None)
    return view


def check_profile(profile: dict, dump: dict, label: str) -> list[str]:
    """Every claim the profile makes about the form, tested against what was observed."""
    problems = []
    view = board_view(profile, dump.get("board"))
    boards = profile.get("boards") or {}
    if boards and dump.get("board") and dump["board"] not in boards:
        problems.append(f"{label} is board {dump['board']!r} but this profile only declares "
                        f"{sorted(boards)}: the section's own ruler is missing, so the checks below are "
                        "the whole-shop defaults")
    tree = dump.get("categoryTree") or {}
    tree_paths = flatten(tree)
    root = (view.get("defaults") or {}).get("rootCategory")
    if root:
        if root in tree:
            pass
        elif root in {part for path in tree_paths for part in path}:
            problems.append(f"profile root category {root!r} exists in {label}'s tree but not as a root "
                            "- the cascader walk would start a level too low")
        else:
            problems.append(f"profile root category {root!r} is not in {label}'s tree")
    scope = (view.get("fields") or {}).get("scope")
    if scope and scope.get("value"):
        # matched by the role the probe recorded, never by guessing which Chinese label means scope
        observed = next((f for f in dump.get("fields") or [] if f.get("role") == "scope"), None)
        if observed is None:
            problems.append(f"profile fills scope {scope['value']!r} but {label} observed no field with "
                            "role 'scope' - either the form dropped it or the probe left the role unmarked "
                            "(fields.without says 'scope' here only if the board really drops it)")
        elif observed.get("options") and scope["value"] not in observed["options"]:
            problems.append(f"profile scope {scope['value']!r} is not among the observed options "
                            f"{observed['options']}")
    order = ((view.get("fields") or {}).get("spec") or {}).get("skuFieldOrder") or []
    columns = dump.get("skuColumns") or []
    if order and len(order) != len(columns):
        problems.append(f"profile skuFieldOrder has {len(order)} entries but {label} shows {len(columns)} "
                        f"columns {columns}")
    sep = ((view.get("fields") or {}).get("category") or {}).get("expectedSeparator")
    if sep and dump.get("separator") and sep != dump["separator"]:
        problems.append(f"profile expectedSeparator {sep!r} != observed separator {dump['separator']!r}")
    observed_trays = index_by(dump.get("trays") or [], lambda t: str(t.get("key")))
    profile_trays = index_by((profile.get("image") or {}).get("trays") or [], lambda t: str(t.get("key")))
    for key in sorted(set(profile_trays) - set(observed_trays)):
        problems.append(f"profile tray {key!r} was not observed in {label}")
    for key in sorted(set(observed_trays) - set(profile_trays)):
        problems.append(f"{label} has tray {key!r} that the profile does not declare")
    for key in sorted(set(profile_trays) & set(observed_trays)):
        a, b = profile_trays[key], observed_trays[key]
        if a.get("crop") and b.get("crop") and a["crop"] != b["crop"]:
            problems.append(f"tray {key}: profile crop {a['crop']!r} but observed {b['crop']!r}")
        if a.get("maxCount") and b.get("maxCount") and a["maxCount"] != b["maxCount"]:
            problems.append(f"tray {key}: profile maxCount {a['maxCount']} but observed {b['maxCount']}")
    name_rules = (view.get("fields") or {}).get("name") or {}
    if name_rules.get("maxLength"):
        # the name field is found by the role the probe marked, never by "first text input that has a
        # maxLength": a code field with its own limit would be measured instead and answer for it
        marked = [f for f in dump.get("fields") or [] if isinstance(f, dict) and f.get("role") == "name"]
        if not marked:
            problems.append(f"profile sets name maxLength {name_rules['maxLength']} but {label} marked no "
                            "field with role 'name' - the probe has to say which field is the name")
        elif len(marked) > 1:
            problems.append(f"{label} marked {len(marked)} fields with role 'name'; the profile's name rules "
                            "can only be checked against one")
        else:
            observed = marked[0].get("maxLength")
            if not observed:
                problems.append(f"profile sets name maxLength {name_rules['maxLength']} but the probe "
                                f"recorded no length limit on {marked[0].get('label')!r}")
            elif observed != name_rules["maxLength"]:
                problems.append(f"profile name maxLength {name_rules['maxLength']} but observed {observed} "
                                f"on {marked[0].get('label')!r}")
    return problems


def capture_note(old: dict, new: dict):
    """Why this pair cannot answer 'did the platform change' - or None when it can."""
    before, after = old.get("capturedAt"), new.get("capturedAt")
    if not before or not after:
        return "at least one of the two dumps carries no capturedAt, so nothing proves they are two " \
               "different moments of the same form"
    if before == after:
        return f"both dumps carry capturedAt {before}, so this is one capture compared with itself"
    return None


def stamp(doc: dict) -> str:
    return f"{doc.get('board') or '?'}@{doc.get('capturedAt') or 'unknown time'}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps", nargs="+",
                    help="one probe dump (what is on the page now) - needs --check-profile, "
                         "or two dumps: the earlier probe first, then the later one")
    ap.add_argument("--check-profile", help="verify this profile.json against the newest dump")
    ap.add_argument("--cross-board", action="store_true",
                    help="the two dumps are from different sections on purpose: read the output as a "
                         "section difference, not as drift")
    ap.add_argument("--out", help="write the machine-readable drift report here")
    ap.add_argument("--verbose", action="store_true", help="also print what did not change")
    args = ap.parse_args()

    new_path = args.dumps[-1]
    old_path = args.dumps[0] if len(args.dumps) == 2 else None
    try:
        if len(args.dumps) > 2:
            raise InputError("pass one dump (with --check-profile) or two: the earlier probe, then the later one")
        new = load_dump(new_path)
        old = load_dump(old_path) if old_path else None
        profile = load_json(args.check_profile) if args.check_profile else None
        if args.check_profile and (not isinstance(profile, dict) or "fields" not in profile):
            raise InputError(f"{args.check_profile}: not a profile (no fields section)")
        if profile is not None:
            reject_secrets(profile, args.check_profile)
        if old is None and profile is None:
            raise InputError("one probe on its own answers nothing: pass the earlier dump to compare against, "
                             "or --check-profile to hold this one against the profile")
    except InputError as exc:
        print(f"cannot compare: {exc}", file=sys.stderr)
        return 2

    label = stamp(new)
    sections, drift, unclear = [], [], None
    if old is not None:
        old_board, new_board = old.get("board"), new.get("board")
        if old_board != new_board and not args.cross_board:
            print(f"cannot compare: {old_path} is board {old_board!r} while {new_path} is board {new_board!r}. "
                  "each board is compared against its own history - otherwise the difference between two "
                  "sections reads as the platform having changed. Use --cross-board to ask that other question.",
                  file=sys.stderr)
            return 2
        unclear = capture_note(old, new)
        sections = [("categories", diff_categories(old, new)), ("fields", diff_fields(old, new)),
                    ("sku columns", diff_sku(old, new)), ("trays", diff_trays(old, new)),
                    ("submit", diff_submit(old, new)), ("observations", diff_observations(old, new))]
        drift = [line for _, lines in sections for line in lines]
        print(f"comparing {old_path} ({stamp(old)}) -> {new_path} ({label})")
        if args.cross_board:
            print("NOTE: --cross-board - what follows is the difference between two sections, "
                  "not one section drifting over time.")
    else:
        print(f"checking one probe: {new_path} ({label})")
    for name, lines in sections:
        if not lines:
            continue
        print(f"\n{name}: {len(lines)}")
        for line in lines:
            print("  ", line)
    if old is not None and not drift and not unclear:
        print("\nno drift between the two probes")

    problems = check_profile(profile, new, label) if profile else []
    if profile:
        if problems:
            print(f"\nprofile vs probe: {len(problems)} inconsistency(ies)")
            for line in problems:
                print("  ", line)
        else:
            print("\nprofile matches the probe")
    if args.verbose and old is not None:
        print(f"\nunchanged: {len(flatten(old.get('categoryTree')) & flatten(new.get('categoryTree')))} category "
              f"paths, {len(set(map(field_key, old.get('fields') or [])) & set(map(field_key, new.get('fields') or [])))} fields, "
              f"{len(old.get('skuColumns') or [])} sku columns")

    code = 1 if (drift or problems) else 0
    if not code and unclear:
        print(f"cannot answer 'did the platform change': {unclear}. Re-export the probe and compare "
              "the two times.", file=sys.stderr)
        code = 2
    if args.out:
        try:
            payload = {"old": str(old_path) if old_path else None, "new": str(new_path), "drift": drift,
                       "profileProblems": problems, "board": new.get("board"),
                       "captureIssue": unclear}
            Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"report written: {args.out}")
        except OSError as exc:
            print(f"WARN cannot write the report to {args.out}: {exc}", file=sys.stderr)
    if code == 1:
        print("\nRESULT: something moved - decide whether to re-probe before batching again")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
