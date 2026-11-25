# SPDX-License-Identifier: MIT
"""XMRig reader add-on: mining numbers from XMRig's built-in web API on this computer.

Read-only: the API runs in "restricted" mode, so it can't change the miner. Nothing here is sent
anywhere except to the hub, and the wallet address isn't read. Output is the same as the
reporter's built-in reader (tests/test_engine.py checks they match).
"""

import json
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:37841"


def is_web_address(url):
    parsed = urllib.parse.urlparse(url or "")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def read_xmrig(url, token=None, timeout=2):
    if not url or url == "off" or not is_web_address(url):
        return {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def get(path):
        request = urllib.request.Request(url.rstrip("/") + path, headers=headers)  # noqa: S310 - checked by is_web_address
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read(500_000))

    try:
        summary = get("/2/summary")
    except (OSError, ValueError):
        return {}
    if not isinstance(summary, dict):
        return {}
    rates = (summary.get("hashrate") or {}).get("total") or []

    def rate(i):
        return rates[i] if len(rates) > i and isinstance(rates[i], (int, float)) else None

    results = summary.get("results") or {}
    connection = summary.get("connection") or {}
    found = {
        "hashrate": rate(1) if rate(1) is not None else rate(0),
        "hashrate_15m": rate(2),
        "shares_good": results.get("shares_good"),
        "shares_total": results.get("shares_total"),
        "pool": connection.get("pool"),
        # XMRig reports the TLS version when the pool link is encrypted, and nothing when it isn't.
        "pool_tls": (connection.get("tls") or "none") if connection.get("pool") else None,
        "rig_id": summary.get("worker_id"),
        "algo": summary.get("algo"),
        "miner_version": summary.get("version"),
    }
    try:
        for backend in get("/2/backends"):
            if backend.get("type") == "cpu" and backend.get("enabled"):
                found["mining_threads"] = len(backend.get("threads") or [])
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return found


def read(settings):
    return read_xmrig(settings.get("url", DEFAULT_URL), settings.get("token"), settings.get("timeout", 2))
