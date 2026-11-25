# SPDX-License-Identifier: MIT
"""Flavors: everything that makes the engine a particular product.

A flavor is one folder with flavor.json (name, command, folders, services, port, update source,
add-ons, plans, console) and words.json (the words its screens use). The engine reads them and
never names a product itself.

A missing or broken flavor stops with a plain message instead of guessing, so a device never runs
under the wrong name or pulls another product's updates.
"""

import json
import re
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]{1,120}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,60}$")


class FlavorError(ValueError):
    """The flavor can't be used; the message says what to fix."""


def _need(data, key, kind, where="flavor.json"):
    value = data.get(key)
    if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
        raise FlavorError(f"{where}: '{key}' is missing or the wrong kind. Fix it and start again.")
    return value


def check_flavor(data):
    """Return the flavor's settings, or raise FlavorError with what's wrong."""
    if not isinstance(data, dict):
        raise FlavorError("flavor.json must be one object. Fix it and start again.")
    product = _need(data, "product", str)
    if not 1 <= len(product) <= 40 or not product.isprintable():
        raise FlavorError("flavor.json: 'product' must be 1 to 40 printable characters.")
    for key in ("name", "command", "service_prefix"):
        if not _NAME_RE.match(_need(data, key, str)):
            raise FlavorError(f"flavor.json: '{key}' may use only lowercase letters, digits and dashes.")
    for key in ("app_dir", "settings_dir", "state_dir"):
        path = _need(data, key, str)
        if not _PATH_RE.match(path) or ".." in path.split("/"):
            raise FlavorError(f"flavor.json: '{key}' must be a full folder path such as /srv/example.")
    port = _need(data, "port", int)
    if not 1024 <= port <= 65535:
        raise FlavorError("flavor.json: 'port' must be from 1024 to 65535.")
    updates = _need(data, "updates", dict)
    source = updates.get("source")
    if source == "github":
        if not _REPO_RE.match(str(updates.get("repo", ""))) or not _BRANCH_RE.match(str(updates.get("branch", ""))):
            raise FlavorError("flavor.json: GitHub updates need 'repo' (owner/name) and 'branch'.")
    elif source != "addon":
        raise FlavorError("flavor.json: 'updates.source' must be 'github' or 'addon' (an update-source add-on).")
    addons = _need(data, "addons", list)
    if any(not isinstance(a, str) or not _NAME_RE.match(a) for a in addons) or len(set(addons)) != len(addons):
        raise FlavorError("flavor.json: 'addons' must be a list of different add-on names.")
    plans = _need(data, "plans", dict)
    for plan, features in plans.items():
        if not _NAME_RE.match(plan) or not isinstance(features, list) or not all(isinstance(f, str) for f in features):
            raise FlavorError(f"flavor.json: plan '{plan}' must list its features as names.")
    console = _need(data, "console", dict)
    if not isinstance(console.get("allowed"), bool) or console.get("default") not in ("off", "on"):
        raise FlavorError("flavor.json: 'console' needs 'allowed' (true or false) and 'default' ('off' or 'on').")
    if console["default"] == "on" and not console["allowed"]:
        raise FlavorError("flavor.json: the console can't default to on when it isn't allowed.")
    _need(data, "legal", dict)
    return data


def load_flavor(folder):
    """Read flavor.json and words.json from a flavor folder."""
    folder = Path(folder)
    try:
        data = json.loads((folder / "flavor.json").read_text(encoding="utf-8"))
        words = json.loads((folder / "words.json").read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise FlavorError(f"The flavor folder {folder} is missing {Path(err.filename).name}. Reinstall to fix it.") from None
    except (OSError, ValueError):
        raise FlavorError(f"The flavor in {folder} can't be read. Reinstall to fix it.") from None
    flavor = check_flavor(data)
    flavor["words"] = check_words(words)
    return flavor


def check_words(words):
    """words.json: each key is a word the screens use, as [one, many] or a single string."""
    if not isinstance(words, dict) or not words:
        raise FlavorError("words.json must be one object with at least one word.")
    for key, value in words.items():
        ok = isinstance(value, str) and value or (
            isinstance(value, list) and len(value) == 2 and all(isinstance(v, str) and v for v in value))
        if not _NAME_RE.match(key.replace("_", "-")) or not ok:
            raise FlavorError(f"words.json: '{key}' must be a word, or [one, many].")
    return words


def say(words, key, count=1):
    """The flavor's word for key: the 'one' form when count is 1, otherwise the 'many' form."""
    value = words.get(key, key)
    if isinstance(value, list):
        return value[0] if count == 1 else value[1]
    return value
