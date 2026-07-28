from __future__ import annotations

import argparse
import csv
import json
import platform
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .backend import BackendError, PacmanBackend
from .catalog import (
    CATALOG_URL,
    Catalog,
    CatalogError,
    default_cache_path,
    download_catalog,
    load_catalog,
    resolve_names,
)
from .completion import script as completion_script
from .health import audit_repository_snapshot, audit_tools
from .output import command_preview, emit_json, error, table
from .profile import ProfileError, read_profile, write_profile
from .repository import (
    REPOSITORY_DB_URL,
    RepositoryError,
    download_repository_database,
    read_repository_database,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blackforge",
        description="Navigate and manage the official BlackArch tool repository.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--catalog", type=Path, help="use a specific catalog JSON file")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--dry-run", action="store_true", help="print package changes without running them")
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="skip confirmation and pass --noconfirm to pacman",
    )
    commands = parser.add_subparsers(dest="command")

    setup = commands.add_parser(
        "setup",
        help="enable and initialize the official BlackArch repository",
    )
    setup.add_argument(
        "--allow-changed-strap",
        action="store_true",
        help="continue when strap.sh differs from the pinned official checksum",
    )

    sync = commands.add_parser(
        "sync",
        aliases=["update-catalog"],
        help="refresh the catalog from blackarch.org",
    )
    sync.add_argument("--url", default=CATALOG_URL)
    sync.add_argument("--output", type=Path, help="write to this path instead of the user cache")

    listing = commands.add_parser("list", help="list catalog tools")
    listing.add_argument("--category")
    listing.add_argument("--limit", type=int, default=100)

    names = commands.add_parser(
        "names",
        help="print package or category names for scripts and shell completion",
    )
    names.add_argument("--prefix", default="")
    names.add_argument("--category")
    names.add_argument("--categories", action="store_true")

    search = commands.add_parser("search", help="search names, descriptions, and categories")
    search.add_argument("query", nargs="+")
    search.add_argument("--category")
    search.add_argument("--limit", type=int, default=50)

    show = commands.add_parser("show", help="show one catalog entry")
    show.add_argument("name")

    commands.add_parser("categories", help="list categories and package counts")
    commands.add_parser("doctor", help="check the host and BlackArch repository setup")

    status = commands.add_parser(
        "status",
        aliases=["check"],
        help="check repository/install state",
    )
    status.add_argument("names", nargs="*")
    status.add_argument("--all", action="store_true", help="audit every catalog entry")
    status.add_argument(
        "--executables",
        action="store_true",
        help="for installed packages, verify declared /usr/bin files without executing them",
    )
    status_source = status.add_mutually_exclusive_group()
    status_source.add_argument(
        "--remote",
        action="store_true",
        help="compare with the live official x86_64 repository database",
    )
    status_source.add_argument(
        "--repo-db",
        type=Path,
        help="compare with a downloaded blackarch.db file",
    )
    status.add_argument("--output", type=Path, help="save the full JSON report")

    install = commands.add_parser(
        "install",
        aliases=["get", "add"],
        help="install packages from BlackArch",
    )
    install.add_argument("names", nargs="*")
    install.add_argument("--category", help="install all tools in a category")
    install.add_argument("--profile", type=Path, help="install packages stored in a profile")
    install.add_argument(
        "--setup-repo",
        action="store_true",
        help="enable the official BlackArch repository first when needed",
    )

    remove = commands.add_parser(
        "remove",
        aliases=["rm", "uninstall"],
        help="remove installed packages",
    )
    remove.add_argument("names", nargs="+")
    remove.add_argument(
        "--purge",
        action="store_true",
        help="also remove now-unused dependencies and config backups (-Rns)",
    )

    upgrade = commands.add_parser("upgrade", help="upgrade the system or selected packages")
    upgrade.add_argument("names", nargs="*")

    repo = commands.add_parser("repo", help="inspect or enable the official BlackArch repository")
    repo_commands = repo.add_subparsers(dest="repo_command", required=True)
    repo_commands.add_parser("status", help="show whether [blackarch] is configured")
    repo_enable = repo_commands.add_parser(
        "enable",
        help="download, verify, inspect, and run the official strap.sh",
    )
    repo_enable.add_argument(
        "--allow-changed-strap",
        action="store_true",
        help="continue when strap.sh differs from the pinned official checksum",
    )

    profile = commands.add_parser("profile", help="create or apply reproducible package profiles")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_create = profile_commands.add_parser("create")
    profile_create.add_argument("path", type=Path)
    profile_create.add_argument("packages", nargs="+")
    profile_create.add_argument("--name")
    profile_show = profile_commands.add_parser("show")
    profile_show.add_argument("path", type=Path)
    profile_apply = profile_commands.add_parser("apply")
    profile_apply.add_argument("path", type=Path)

    export = commands.add_parser("export", help="export the catalog as JSON or CSV")
    export.add_argument("path", type=Path)
    export.add_argument("--format", choices=("json", "csv"), default="json")

    completion = commands.add_parser(
        "completion",
        help="print a bash, zsh, or fish completion script",
    )
    completion.add_argument("shell", choices=("bash", "zsh", "fish"))

    commands.add_parser("interactive", help="open a guided terminal menu")
    return parser


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        raise BackendError("Confirmation requires a terminal; use --yes or --dry-run")
    answer = input(f"{prompt} [y/N] ").strip().casefold()
    return answer in {"y", "yes"}


def _display_tools(tools: Sequence, as_json: bool) -> None:
    if as_json:
        emit_json([tool.to_dict() for tool in tools])
        return
    table(
        ["Name", "Version", "Category", "Description"],
        ((tool.name, tool.version, tool.category, tool.description) for tool in tools),
    )


def _select_tools(args: argparse.Namespace, catalog: Catalog) -> list:
    if args.names:
        return resolve_names(catalog, args.names)
    if getattr(args, "category", None):
        if args.category not in catalog.categories:
            raise CatalogError(f"Unknown category: {args.category}")
        return catalog.categories[args.category]
    if getattr(args, "profile", None):
        _, names = read_profile(args.profile)
        return resolve_names(catalog, names)
    raise CatalogError("Provide package names, --category, or --profile")


def _run_install(args: argparse.Namespace, catalog: Catalog, backend: PacmanBackend) -> int:
    tools = _select_tools(args, catalog)
    names = [tool.name for tool in tools]
    command = ["sudo", "pacman", "-S", "--needed", *(["--noconfirm"] if args.yes else []), *names]
    if args.dry_run:
        if getattr(args, "setup_repo", False) and not backend.repo_enabled:
            print("Would enable the official BlackArch repository first.")
        print(f"Would install {len(names)} package(s):")
        print(command_preview(command))
        return 0
    backend.require_supported()
    if not backend.repo_enabled:
        if getattr(args, "setup_repo", False):
            setup_args = argparse.Namespace(
                allow_changed_strap=False,
                dry_run=False,
                yes=args.yes,
            )
            setup_result = _repo_enable(setup_args, backend)
            if setup_result != 0:
                return setup_result
        else:
            raise BackendError(
                "The [blackarch] repository is not enabled. Run `blackforge setup` "
                "once, or add `--setup-repo` to this install command."
            )
    if not args.yes and not _confirm(f"Install {len(names)} package(s) from BlackArch?"):
        print("Cancelled.")
        return 1
    result = backend.install(names)
    return result.returncode


def _doctor(backend: PacmanBackend, catalog: Catalog, as_json: bool) -> int:
    checks = {
        "operating_system": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "pacman_found": shutil.which("pacman") is not None,
        "sudo_found": shutil.which("sudo") is not None,
        "blackarch_repo_enabled": backend.repo_enabled,
        "catalog_tools": len(catalog.tools),
        "catalog_fetched_at": catalog.fetched_at,
        "catalog_source": catalog.source,
    }
    if as_json:
        emit_json(checks)
    else:
        table(["Check", "Value"], checks.items())
    return 0 if backend.supported and backend.repo_enabled else 2


def _repo_enable(args: argparse.Namespace, backend: PacmanBackend) -> int:
    if backend.repo_enabled:
        print("The [blackarch] repository is already configured.")
        return 0
    backend.require_supported()
    script, digest, sha1 = backend.download_strap(
        allow_changed=args.allow_changed_strap
    )
    try:
        print(f"Downloaded official script: {script}")
        print(f"SHA-256: {digest}")
        print(f"Official SHA-1 check: {sha1} (matched)")
        preview = "\n".join(script.read_text(encoding="utf-8", errors="replace").splitlines()[:12])
        print("\nFirst 12 lines:\n")
        print(preview)
        if args.dry_run:
            print("\nDry run: the script was not executed.")
            return 0
        if not args.yes and not _confirm("Run this official BlackArch setup script as root?"):
            print("Cancelled.")
            return 1
        return backend.enable_repo(script).returncode
    finally:
        try:
            script.unlink()
        except OSError:
            pass


def _interactive(catalog: Catalog, backend: PacmanBackend, args: argparse.Namespace) -> int:
    while True:
        print(
            "\nBlackForge\n"
            f"Catalog: {len(catalog.tools)} tools / {len(catalog.categories)} categories\n"
            "1) Search tools\n"
            "2) Browse categories\n"
            "3) System doctor\n"
            "4) Repository status\n"
            "5) Quit"
        )
        choice = input("> ").strip()
        if choice == "1":
            query = input("Search: ").strip()
            _display_tools(catalog.search(query, limit=25), False)
        elif choice == "2":
            table(
                ["Category", "Tools"],
                ((name, len(tools)) for name, tools in catalog.categories.items()),
            )
            category = input("Category (blank to go back): ").strip()
            if category:
                _display_tools(catalog.categories.get(category, [])[:100], False)
        elif choice == "3":
            _doctor(backend, catalog, False)
        elif choice == "4":
            print("enabled" if backend.repo_enabled else "not enabled")
        elif choice in {"5", "q", "quit"}:
            return 0


def run(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        if sys.stdin.isatty():
            args.command = "interactive"
        else:
            parser.print_help()
            return 0

    backend = PacmanBackend(dry_run=args.dry_run, assume_yes=args.yes)

    if args.command in {"sync", "update-catalog"}:
        catalog = download_catalog(args.url)
        destination = args.output or default_cache_path()
        catalog.write(destination)
        result = {"path": str(destination), "tools": len(catalog.tools), "source": catalog.source}
        emit_json(result) if args.json else print(
            f"Synced {len(catalog.tools)} tools to {destination}"
        )
        return 0

    catalog = load_catalog(args.catalog)
    if args.command == "setup":
        return _repo_enable(args, backend)
    if args.command == "list":
        tools = catalog.tools
        if args.category:
            if args.category not in catalog.categories:
                raise CatalogError(f"Unknown category: {args.category}")
            tools = catalog.categories[args.category]
        _display_tools(tools[: args.limit] if args.limit else tools, args.json)
        return 0
    if args.command == "names":
        prefix = args.prefix.casefold()
        if args.categories:
            values = catalog.categories
        else:
            tools = (
                catalog.categories.get(args.category, [])
                if args.category
                else catalog.tools
            )
            if args.category and args.category not in catalog.categories:
                raise CatalogError(f"Unknown category: {args.category}")
            values = [tool.name for tool in tools]
        for value in values:
            if value.casefold().startswith(prefix):
                print(value)
        return 0
    if args.command == "search":
        tools = catalog.search(" ".join(args.query), category=args.category, limit=args.limit)
        _display_tools(tools, args.json)
        return 0 if tools else 1
    if args.command == "show":
        tools = resolve_names(catalog, [args.name])
        _display_tools(tools, args.json)
        return 0
    if args.command == "categories":
        values = [
            {"category": name, "tools": len(tools)}
            for name, tools in catalog.categories.items()
        ]
        if args.json:
            emit_json(values)
        else:
            table(["Category", "Tools"], ((item["category"], item["tools"]) for item in values))
        return 0
    if args.command == "doctor":
        return _doctor(backend, catalog, args.json)
    if args.command in {"status", "check"}:
        if not args.names and not args.all:
            raise CatalogError("Provide package names or use --all")
        tools = catalog.tools if args.all else resolve_names(catalog, args.names)
        if args.remote or args.repo_db:
            if args.executables:
                raise CatalogError("--executables requires local pacman, not --remote/--repo-db")
            snapshot = (
                read_repository_database(args.repo_db)
                if args.repo_db
                else download_repository_database(REPOSITORY_DB_URL)
            )
            audit = audit_repository_snapshot(tools, snapshot)
        else:
            audit = audit_tools(tools, backend, check_executables=args.executables)
        payload = audit.to_dict()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if args.json:
            emit_json(payload)
        else:
            table(
                ["Package", "Status", "Catalog", "Repository", "Installed", "Note"],
                (
                    (
                        state.name,
                        state.status,
                        state.catalog_version,
                        state.repository_version or "-",
                        state.installed_version or "-",
                        state.note,
                    )
                    for state in audit.states
                ),
            )
            print("\nSummary: " + ", ".join(f"{key}={value}" for key, value in audit.counts.items()))
        return 0 if not any(
            state.status in {"missing-from-repo", "installed-files-missing"}
            for state in audit.states
        ) else 3
    if args.command in {"install", "get", "add"}:
        return _run_install(args, catalog, backend)
    if args.command in {"remove", "rm", "uninstall"}:
        resolve_names(catalog, args.names)
        if args.dry_run:
            operation = "-Rns" if args.purge else "-R"
            print(command_preview(["sudo", "pacman", operation, *args.names]))
            return 0
        if not args.yes and not _confirm(f"Remove {len(args.names)} package(s)?"):
            print("Cancelled.")
            return 1
        return backend.remove(args.names, purge=args.purge).returncode
    if args.command == "upgrade":
        if args.names:
            resolve_names(catalog, args.names)
        if args.dry_run:
            operation = "-S" if args.names else "-Syu"
            print(command_preview(["sudo", "pacman", operation, *args.names]))
            return 0
        if not args.yes and not _confirm(
            "Upgrade selected packages?" if args.names else "Run a full system upgrade?"
        ):
            print("Cancelled.")
            return 1
        return backend.upgrade(args.names).returncode
    if args.command == "repo":
        if args.repo_command == "status":
            value = {"enabled": backend.repo_enabled, "supported": backend.supported}
            emit_json(value) if args.json else print(
                "enabled" if value["enabled"] else "not enabled"
            )
            return 0 if value["enabled"] else 2
        return _repo_enable(args, backend)
    if args.command == "profile":
        if args.profile_command == "create":
            tools = resolve_names(catalog, args.packages)
            write_profile(args.path, args.name or args.path.stem, [tool.name for tool in tools])
            print(f"Saved {len(tools)} packages to {args.path}")
            return 0
        name, packages = read_profile(args.path)
        resolve_names(catalog, packages)
        if args.profile_command == "show":
            payload = {"name": name, "packages": packages}
            emit_json(payload) if args.json else _display_tools(
                resolve_names(catalog, packages), False
            )
            return 0
        install_args = argparse.Namespace(**vars(args))
        install_args.names = []
        install_args.category = None
        install_args.profile = args.path
        install_args.setup_repo = False
        return _run_install(install_args, catalog, backend)
    if args.command == "export":
        args.path.parent.mkdir(parents=True, exist_ok=True)
        if args.format == "json":
            catalog.write(args.path)
        else:
            with args.path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["name", "version", "description", "category", "website"],
                )
                writer.writeheader()
                writer.writerows(tool.to_dict() for tool in catalog.tools)
        print(f"Exported {len(catalog.tools)} tools to {args.path}")
        return 0
    if args.command == "completion":
        print(completion_script(args.shell), end="")
        return 0
    if args.command == "interactive":
        return _interactive(catalog, backend, args)
    parser.error("Unknown command")
    return 2


def main() -> None:
    try:
        raise SystemExit(run())
    except (BackendError, CatalogError, ProfileError, RepositoryError) as exc:
        error(str(exc))
        raise SystemExit(2) from exc
