"""Read-only, opt-in access to official AUR RPC metadata."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

AUR_RPC = "https://aur.archlinux.org/rpc/v5"
_PACKAGE = re.compile(r"^[A-Za-z0-9@._+-]+$")


class AurError(RuntimeError):
    pass


def _request(path: str, *, timeout: int = 10) -> list[dict[str, Any]]:
    url = f"{AUR_RPC}/{path}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "BlackForge/0.4"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.url.startswith(f"{AUR_RPC}/") is False:
                raise AurError("AUR RPC redirected outside the official endpoint")
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AurError(f"Unable to query the official AUR RPC: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 5:
        raise AurError("The AUR RPC returned an unsupported response")
    if payload.get("type") == "error":
        raise AurError(str(payload.get("error", "AUR RPC error")))
    results = payload.get("results")
    if not isinstance(results, list) or any(
        not isinstance(item, dict) for item in results
    ):
        raise AurError("The AUR RPC returned malformed results")
    return results


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, int) or value < 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _normalize(item: dict[str, Any]) -> dict[str, object]:
    name = str(item.get("Name", ""))
    return {
        "name": name,
        "version": str(item.get("Version", "")),
        "description": str(item.get("Description") or ""),
        "maintainer": item.get("Maintainer"),
        "votes": int(item.get("NumVotes", 0)),
        "popularity": float(item.get("Popularity", 0.0)),
        "out_of_date": _timestamp(item.get("OutOfDate")),
        "first_submitted": _timestamp(item.get("FirstSubmitted")),
        "last_modified": _timestamp(item.get("LastModified")),
        "url": f"https://aur.archlinux.org/packages/{urllib.parse.quote(name)}",
        "metadata_only": True,
    }


def search_aur(query: str, *, limit: int = 25) -> list[dict[str, object]]:
    query = query.strip()
    if len(query) < 2 or len(query) > 100:
        raise AurError("AUR searches must contain 2 to 100 characters")
    path = f"search/{urllib.parse.quote(query, safe='')}?by=name-desc"
    return [_normalize(item) for item in _request(path)[:limit]]


def aur_info(name: str) -> dict[str, object]:
    if not _PACKAGE.fullmatch(name):
        raise AurError(f"Invalid AUR package name: {name!r}")
    query = urllib.parse.urlencode({"arg[]": name})
    results = _request(f"info?{query}")
    if not results:
        raise AurError(f"No AUR package named {name!r}")
    return _normalize(results[0])
