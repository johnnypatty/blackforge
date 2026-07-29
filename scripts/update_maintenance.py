#!/usr/bin/env python3
"""Build evidence-based maintenance metadata from supported upstream hosts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "src" / "blackforge" / "data" / "tools.json"
DEFAULT_OUTPUT = ROOT / "src" / "blackforge" / "data" / "maintenance.json"


def github_repository(url: str) -> tuple[str, str] | None:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if (parsed.hostname or "").casefold() not in {"github.com", "www.github.com"}:
        return None
    parts = [urllib.parse.unquote(item) for item in parsed.path.split("/") if item]
    if len(parts) < 2:
        return None
    owner, name = parts[:2]
    name = name.removesuffix(".git")
    if not owner or not name:
        return None
    return owner, name


def query_batch(
    repositories: list[tuple[str, str]],
    *,
    gh: str,
    retries: int = 3,
) -> dict[tuple[str, str], dict[str, object] | None]:
    aliases: list[tuple[str, tuple[str, str]]] = []
    fields: list[str] = []
    for index, (owner, name) in enumerate(repositories):
        alias = f"r{index}"
        aliases.append((alias, (owner, name)))
        fields.append(
            f"{alias}: search(query: {json.dumps(f'repo:{owner}/{name}')}, "
            "type: REPOSITORY, first: 1) { nodes { ... on Repository { "
            "nameWithOwner url pushedAt isArchived isDisabled } } }"
        )
    query = "query MaintenanceAudit { " + " ".join(fields) + " }"
    last_error = ""
    for attempt in range(retries):
        completed = subprocess.run(
            [gh, "api", "graphql", "-f", f"query={query}"],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode == 0:
            try:
                value = json.loads(completed.stdout)
                data = value["data"]
                if not isinstance(data, dict):
                    raise TypeError("GraphQL data is not an object")
                result: dict[tuple[str, str], dict[str, object] | None] = {}
                for alias, repository in aliases:
                    search_value = data.get(alias)
                    nodes = (
                        search_value.get("nodes")
                        if isinstance(search_value, dict)
                        else None
                    )
                    candidate = (
                        nodes[0]
                        if isinstance(nodes, list)
                        and nodes
                        and isinstance(nodes[0], dict)
                        else None
                    )
                    expected = f"{repository[0]}/{repository[1]}".casefold()
                    actual = (
                        str(candidate.get("nameWithOwner", "")).casefold()
                        if candidate
                        else ""
                    )
                    result[repository] = candidate if actual == expected else None
                return result
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                last_error = str(exc)
        else:
            last_error = (completed.stderr or completed.stdout).strip()
        if attempt + 1 < retries:
            time.sleep(2**attempt)
    raise RuntimeError(f"GitHub metadata query failed: {last_error}")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def safe_evidence_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        return ""
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--stale-years", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--sanitize-existing",
        action="store_true",
        help="normalize unsafe evidence URLs in the existing output without network access",
    )
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 75:
        parser.error("--batch-size must be between 1 and 75")
    if args.stale_years < 1:
        parser.error("--stale-years must be positive")
    if args.sanitize_existing:
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        records = existing.get("records")
        if not isinstance(records, dict):
            parser.error("existing output has no records object")
        changed = 0
        for record in records.values():
            if not isinstance(record, dict):
                parser.error("existing output contains a malformed record")
            original = str(record.get("evidence_url", ""))
            normalized = safe_evidence_url(original)
            if normalized != original:
                record["evidence_url"] = normalized
                changed += 1
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(args.output)
        print(f"Sanitized {changed} evidence URLs in {args.output}")
        return 0

    value = json.loads(args.catalog.read_text(encoding="utf-8"))
    tools = value.get("tools")
    if not isinstance(tools, list):
        parser.error("catalog has no tools list")
    if args.limit:
        tools = tools[: args.limit]

    repository_tools: dict[tuple[str, str], list[str]] = {}
    websites: dict[str, str] = {}
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        website = item.get("website", "")
        if not isinstance(name, str) or not isinstance(website, str):
            continue
        websites[name] = website
        repository = github_repository(website)
        if repository:
            repository_tools.setdefault(repository, []).append(name)

    repositories = sorted(repository_tools)
    metadata: dict[tuple[str, str], dict[str, object] | None] = {}
    for start in range(0, len(repositories), args.batch_size):
        batch = repositories[start : start + args.batch_size]
        metadata.update(query_batch(batch, gh=args.gh))
        print(
            f"Checked {min(start + len(batch), len(repositories))}/"
            f"{len(repositories)} GitHub repositories",
            file=sys.stderr,
        )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        cutoff = now.replace(year=now.year - args.stale_years)
    except ValueError:
        cutoff = now.replace(month=2, day=28, year=now.year - args.stale_years)
    checked_at = now.isoformat()
    records: dict[str, dict[str, object]] = {}
    for name, website in websites.items():
        repository = github_repository(website)
        item = metadata.get(repository) if repository else None
        if repository is None:
            records[name] = {
                "status": "unknown",
                "last_activity_at": None,
                "checked_at": checked_at,
                "evidence_url": safe_evidence_url(website),
                "evidence_kind": "unsupported-upstream",
                "confidence": "none",
                "note": "No supported upstream activity source was available.",
            }
            continue
        if item is None:
            records[name] = {
                "status": "unknown",
                "last_activity_at": None,
                "checked_at": checked_at,
                "evidence_url": safe_evidence_url(website),
                "evidence_kind": "github-repository-unavailable",
                "confidence": "low",
                "note": "The referenced GitHub repository was unavailable or inaccessible.",
            }
            continue
        pushed_at = item.get("pushedAt")
        archived = bool(item.get("isArchived") or item.get("isDisabled"))
        status = "archived" if archived else "unknown"
        last_activity_at = pushed_at if isinstance(pushed_at, str) else None
        if not archived and last_activity_at:
            status = "current" if parse_time(last_activity_at) >= cutoff else "stale"
        records[name] = {
            "status": status,
            "last_activity_at": last_activity_at,
            "checked_at": checked_at,
            "evidence_url": str(item.get("url") or website),
            "evidence_kind": "github-pushed-at",
            "confidence": "high" if last_activity_at else "low",
            "note": (
                "Classification uses the upstream repository's latest code push. "
                "It does not prove runtime compatibility."
            ),
        }

    counts: dict[str, int] = {}
    for record in records.values():
        status = str(record["status"])
        counts[status] = counts.get(status, 0) + 1
    output = {
        "schema_version": 1,
        "generated_at": checked_at,
        "stale_years": args.stale_years,
        "cutoff": cutoff.isoformat(),
        "source": "GitHub GraphQL repository pushedAt/archive metadata",
        "counts": dict(sorted(counts.items())),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(f"Wrote {len(records)} maintenance records to {args.output}")
    print("Counts: " + ", ".join(f"{key}={count}" for key, count in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
