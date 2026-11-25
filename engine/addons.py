# SPDX-License-Identifier: MIT
"""Add-ons: how a flavor adds readings without editing the engine.

An add-on is one folder with addon.json and reader.py. addon.json names it, says its kind and
declares every report field it may add, with limits. reader.py has read(settings) that returns a
dict of those fields. The engine keeps only declared fields, and a broken add-on is switched off
with a plain message while everything else keeps running.

Add-ons are loaded only from the app's own add-ons folder, by names the flavor lists.
"""

import importlib.util
import json
import re
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
KINDS = ("reader",)
FIELD_TYPES = ("number", "text", "object")


class Addon:
    def __init__(self, name, kind, fields, read, folder):
        self.name, self.kind, self.fields, self.read, self.folder = name, kind, fields, read, folder


def _check_manifest(name, data):
    if not isinstance(data, dict) or data.get("name") != name:
        raise ValueError("its addon.json must give the same name as its folder")
    if data.get("kind") not in KINDS:
        raise ValueError(f"its kind must be one of: {', '.join(KINDS)}")
    fields = data.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("it must declare the report fields it adds")
    for field, spec in fields.items():
        if not _FIELD_RE.match(field) or not isinstance(spec, dict) or spec.get("type") not in FIELD_TYPES:
            raise ValueError(f"field '{field}' needs a type ({', '.join(FIELD_TYPES)})")
    return fields


def load_addons(folder, names):
    """Load the named add-ons. Returns (add-ons, problems); each problem is a plain sentence."""
    folder = Path(folder)
    loaded, problems = [], []
    for name in names:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            problems.append(f"The add-on name {name!r} isn't allowed, so it was skipped.")
            continue
        here = folder / name
        try:
            fields = _check_manifest(name, json.loads((here / "addon.json").read_text(encoding="utf-8")))
            spec = importlib.util.spec_from_file_location(f"fleet_addon_{name.replace('-', '_')}", here / "reader.py")
            if spec is None or spec.loader is None:
                raise ValueError("its reader.py can't be loaded")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            read = getattr(module, "read", None)
            if not callable(read):
                raise ValueError("its reader.py has no read() function")
        except Exception as err:  # noqa: BLE001 - any broken add-on is switched off, never fatal
            problems.append(f"The add-on '{name}' was switched off: {err}. Reinstall it or remove it from the flavor.")
            continue
        loaded.append(Addon(name, "reader", fields, read, here))
    return loaded, problems


def run_readers(addons, settings=None):
    """Run every reader add-on. Returns (readings, problems); only declared fields are kept."""
    readings, problems = {}, []
    for addon in addons:
        try:
            found = addon.read(settings or {})
        except Exception as err:  # noqa: BLE001 - one failing add-on never stops the others
            problems.append(f"The add-on '{addon.name}' failed this time ({type(err).__name__}).")
            continue
        if not isinstance(found, dict):
            continue
        for key, value in found.items():
            if key in addon.fields:
                readings[key] = value
    return readings, problems
