from __future__ import annotations

import argparse
import csv
import io
import platform
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .backend import (
    BackendError,
    PacmanBackend,
    validate_package_names,
)
from .catalog import (
    CATALOG_URL,
    Catalog,
    CatalogError,
    bundled_catalog,
    default_cache_path,
    download_catalog,
    load_catalog,
    resolve_names,
)
from .completion import script as completion_script
from .environment import (
    EnvironmentFileError,
    PackageRef,
    create_environment,
    export_environment,
    plan_environment_import,
    read_environment,
)
from .health import audit_repository_snapshot, audit_tools
from .history import HistoryError, HistoryStore, make_history_record, plan_undo
from .maintenance import MaintenanceError, load_bundled_maintenance
from .mirrors import (
    DEFAULT_MIRRORLIST,
    MirrorError,
    apply_mirror,
    list_mirrors,
    recommend_mirror,
    test_mirrors,
)
from .output import command_preview, emit_json, error, table
from .planner import PlannerError, plan_install, plan_remove, plan_upgrade
from .presets import PresetError, list_presets, resolve_preset, resolve_preset_packages
from .profile import ProfileError, read_profile, write_profile
from .repository import (
    REPOSITORY_DB_URL,
    RepositoryError,
    download_repository_database,
    read_repository_database,
)
from .self_update import SelfUpdateError, apply_release, check_latest
from .sources import (
    ArchTool,
    SourceError,
    bundled_arch_catalog,
    is_curated_arch_tool,
    list_arch_tools,
    resolve_arch_tool,
)
from .storage import atomic_write_json, atomic_write_text
from .transactions import TransactionError, TransactionJournal
from .tui import TuiError, run_tui
from .updates import UpdateError, compare_catalogs, read_report, save_report


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if result < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return result


def _retry_count(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 0 <= result <= 9:
        raise argparse.ArgumentTypeError("must be between 0 and 9")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blackforge",
        description="Navigate and manage the official BlackArch tool repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Start here:\n"
            "  blackforge help install\n"
            "  blackforge search \"network mapper\"\n"
            "  blackforge --dry-run install amass\n"
            "  blackforge doctor\n\n"
            "Security tools must only be used on systems you own or are authorized to test."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--catalog", type=Path, help="use a specific catalog JSON file")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview the command without changing packages or files",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="skip confirmation and pass --noconfirm to pacman",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    help_command = commands.add_parser(
        "help",
        help="show clear help for BlackForge or a specific command",
        description=(
            "Show command-specific usage, options, safety notes, and examples.\n\n"
            "Examples:\n"
            "  blackforge help install\n"
            "  blackforge help profile create\n"
            "  blackforge help --all"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    help_command.add_argument("path", nargs="*", metavar="COMMAND")
    help_command.add_argument(
        "--all",
        action="store_true",
        help="print help for every canonical command and subcommand",
    )
    commands.add_parser("version", help="print the installed BlackForge version")

    setup = commands.add_parser(
        "setup",
        help="enable and initialize the official BlackArch repository",
    )
    setup.add_argument(
        "--strap-sha256",
        metavar="SHA256",
        help="approve one manually reviewed strap.sh by its exact SHA-256",
    )

    sync = commands.add_parser(
        "sync",
        aliases=["update-catalog"],
        help="refresh the catalog from blackarch.org",
    )
    sync.add_argument("--url", default=CATALOG_URL, help="HTTPS catalog URL")
    sync.add_argument(
        "--output",
        type=Path,
        help="write to this path instead of the user cache",
    )

    listing = commands.add_parser("list", help="list catalog tools")
    listing.add_argument("--category", help="filter by one exact category")
    listing.add_argument(
        "--source",
        choices=("all", "blackarch", "arch"),
        default="all",
        help="select BlackArch, curated official Arch, or both",
    )
    listing.add_argument(
        "--limit",
        type=_positive_int,
        default=100,
        help="maximum rows to show (default: 100)",
    )

    names = commands.add_parser(
        "names",
        help="print package or category names for scripts and shell completion",
    )
    names.add_argument("--prefix", default="", help="keep names with this prefix")
    names.add_argument("--category", help="print names from one exact category")
    names.add_argument(
        "--categories",
        action="store_true",
        help="print category names instead of package names",
    )

    search = commands.add_parser("search", help="search names, descriptions, and categories")
    search.add_argument("query", nargs="+", help="one or more search terms")
    search.add_argument("--category", help="restrict results to one category")
    search.add_argument(
        "--source",
        choices=("all", "blackarch", "arch"),
        default="all",
        help="select BlackArch, curated official Arch, or both",
    )
    search.add_argument(
        "--limit",
        type=_positive_int,
        default=50,
        help="maximum matches to show (default: 50)",
    )

    show = commands.add_parser(
        "show",
        aliases=["info"],
        help="show detailed package, source, and maintenance information",
    )
    show.add_argument("name", help="package name, optionally source-qualified")

    categories = commands.add_parser("categories", help="list categories and package counts")
    categories.add_argument(
        "--source",
        choices=("all", "blackarch", "arch"),
        default="all",
        help="select BlackArch, curated official Arch, or both",
    )
    commands.add_parser("doctor", help="check the host and BlackArch repository setup")

    status = commands.add_parser(
        "status",
        aliases=["check"],
        help="check repository/install state",
    )
    status.add_argument(
        "names",
        nargs="*",
        help="BlackArch package names to audit; alternatively use --all",
    )
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
        help="install BlackArch or curated official Arch packages",
        description=(
            "Resolve reviewed package names, show the exact pacman transaction, "
            "then install after confirmation.\n\n"
            "Examples:\n"
            "  blackforge --dry-run install amass\n"
            "  blackforge install arch:nmap\n"
            "  blackforge -y install --setup-repo amass"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    install.add_argument(
        "names",
        nargs="*",
        help="package names or source-qualified references such as arch:nmap",
    )
    install.add_argument("--category", help="install all tools in a category")
    install.add_argument("--profile", type=Path, help="install packages stored in a profile")
    install.add_argument(
        "--retries",
        type=_retry_count,
        default=2,
        help="maximum retries for recognized network/download failures",
    )
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
    remove.add_argument(
        "names",
        nargs="+",
        help="installed package names or source-qualified references",
    )
    remove.add_argument(
        "--purge",
        action="store_true",
        help="also remove now-unused dependencies and config backups (-Rns)",
    )

    upgrade = commands.add_parser("upgrade", help="upgrade the system or selected packages")
    upgrade.add_argument(
        "names",
        nargs="*",
        help="optional package names; omit them for a full Arch system upgrade",
    )

    repo = commands.add_parser("repo", help="inspect or enable the official BlackArch repository")
    repo_commands = repo.add_subparsers(dest="repo_command", required=True)
    repo_commands.add_parser("status", help="show whether [blackarch] is configured")
    repo_enable = repo_commands.add_parser(
        "enable",
        help="download, verify, inspect, and run the official strap.sh",
    )
    repo_enable.add_argument(
        "--strap-sha256",
        metavar="SHA256",
        help="approve one manually reviewed strap.sh by its exact SHA-256",
    )

    profile = commands.add_parser("profile", help="create or apply reproducible package profiles")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_create = profile_commands.add_parser(
        "create",
        help="validate and save a reusable package list",
    )
    profile_create.add_argument("path", type=Path, help="destination JSON file")
    profile_create.add_argument(
        "packages",
        nargs="+",
        help="BlackArch or curated official Arch package references",
    )
    profile_create.add_argument("--name", help="friendly profile name")
    profile_show = profile_commands.add_parser(
        "show",
        help="validate and display a saved profile",
    )
    profile_show.add_argument("path", type=Path, help="profile JSON file")
    profile_apply = profile_commands.add_parser(
        "apply",
        help="plan, confirm, and install a saved profile",
    )
    profile_apply.add_argument("path", type=Path, help="profile JSON file")

    export = commands.add_parser("export", help="export the catalog as JSON or CSV")
    export.add_argument("path", type=Path, help="destination file")
    export.add_argument(
        "--format",
        choices=("json", "csv"),
        default="json",
        help="output format (default: json)",
    )

    completion = commands.add_parser(
        "completion",
        help="print a bash, zsh, or fish completion script",
    )
    completion.add_argument("shell", choices=("bash", "zsh", "fish"))

    commands.add_parser("interactive", help="open a guided terminal menu")
    commands.add_parser(
        "tui",
        help="open the full-screen searchable terminal interface",
        description=(
            "Browse, filter, inspect, and select tools in a full-screen Linux terminal.\n"
            "Keys: arrows/j/k move, / searches, space selects, i shows details,\n"
            "Enter continues with a safe install preview, and q quits."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    plan = commands.add_parser(
        "plan",
        help="preview dependencies, sizes, conflicts, and the exact pacman operation",
    )
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    for action in ("install", "remove", "upgrade"):
        action_parser = plan_commands.add_parser(
            action,
            help=f"preview a package {action} without changing the system",
        )
        action_parser.add_argument(
            "names",
            nargs="*" if action == "upgrade" else "+",
            help=(
                "package names or source-qualified references; omit them only "
                "for a full-system upgrade plan"
            ),
        )
        if action == "remove":
            action_parser.add_argument(
                "--purge",
                action="store_true",
                help="preview pacman -Rns instead of conservative -R",
            )

    history = commands.add_parser(
        "history",
        help="inspect recorded package transactions and create safe undo plans",
    )
    history_commands = history.add_subparsers(dest="history_command", required=True)
    history_list = history_commands.add_parser("list", help="list recent transactions")
    history_list.add_argument(
        "--limit",
        type=_positive_int,
        default=25,
        help="maximum records to show (default: 25)",
    )
    history_show = history_commands.add_parser("show", help="show one transaction")
    history_show.add_argument("transaction_id", help="recorded transaction ID")
    history_undo = history_commands.add_parser(
        "undo",
        help="plan or apply the safest available inverse operation",
    )
    history_undo.add_argument("transaction_id", help="recorded transaction ID")
    history_undo.add_argument(
        "--apply",
        action="store_true",
        help="apply only a complete, exact, state-checked inverse",
    )

    resume = commands.add_parser(
        "resume",
        help="inspect or resume a retryable failed package transaction",
    )
    resume.add_argument(
        "transaction_id",
        nargs="?",
        help="specific transaction ID (default: newest failed transaction)",
    )
    resume.add_argument(
        "--apply",
        action="store_true",
        help="resume when the recorded failure and retry limit permit it",
    )

    mirror = commands.add_parser(
        "mirror",
        help="list, test, recommend, or safely select BlackArch mirrors",
    )
    mirror_commands = mirror.add_subparsers(dest="mirror_command", required=True)
    mirror_list = mirror_commands.add_parser("list", help="list configured mirrors")
    mirror_list.add_argument("--path", type=Path, help="alternate mirror-list path")
    mirror_test = mirror_commands.add_parser("test", help="measure HTTPS mirrors")
    mirror_test.add_argument("--path", type=Path, help="alternate mirror-list path")
    mirror_test.add_argument(
        "--timeout",
        type=_positive_int,
        default=5,
        help="per-request timeout in seconds (default: 5)",
    )
    mirror_recommend = mirror_commands.add_parser(
        "recommend",
        help="show the best responsive HTTPS mirror",
    )
    mirror_recommend.add_argument(
        "--path",
        type=Path,
        help="alternate mirror-list path",
    )
    mirror_apply = mirror_commands.add_parser(
        "apply",
        help="select one mirror after creating an atomic backup",
    )
    mirror_apply.add_argument("url", help="exact HTTPS URL already present in the list")
    mirror_apply.add_argument("--path", type=Path, help="alternate mirror-list path")

    updates = commands.add_parser(
        "updates",
        help="check or show BlackArch catalog changes",
    )
    updates_commands = updates.add_subparsers(dest="updates_command", required=True)
    updates_check = updates_commands.add_parser(
        "check",
        help="compare the bundled catalog with the live official catalog",
    )
    updates_check.add_argument(
        "--url",
        default=CATALOG_URL,
        help="HTTPS catalog URL to compare",
    )
    updates_commands.add_parser("show", help="show the last saved change report")

    self_update = commands.add_parser(
        "self-update",
        help="check for or securely apply a BlackForge release",
    )
    self_update_mode = self_update.add_mutually_exclusive_group()
    self_update_mode.add_argument(
        "--check",
        action="store_true",
        help="check only (also the default)",
    )
    self_update_mode.add_argument(
        "--apply",
        action="store_true",
        help="verify and install an available release",
    )

    environment = commands.add_parser(
        "env",
        help="export or safely reproduce an Arch security-tool environment",
    )
    environment_commands = environment.add_subparsers(
        dest="environment_command",
        required=True,
    )
    environment_export = environment_commands.add_parser(
        "export",
        help="record explicitly installed security packages",
    )
    environment_export.add_argument("path", type=Path, help="destination JSON manifest")
    environment_import = environment_commands.add_parser(
        "import",
        help="validate and plan an exported environment",
    )
    environment_import.add_argument("path", type=Path, help="environment JSON manifest")
    environment_import.add_argument(
        "--apply",
        action="store_true",
        help="apply the reviewed plan; import is plan-only by default",
    )
    environment_import.add_argument(
        "--allow-newer",
        action="store_true",
        help="accept current signed rolling-repository versions",
    )

    maintenance = commands.add_parser(
        "maintenance",
        help="browse Recently maintained and Needs attention tool groups",
    )
    maintenance_commands = maintenance.add_subparsers(
        dest="maintenance_command",
        required=True,
    )
    maintenance_summary = maintenance_commands.add_parser(
        "summary",
        help="show maintenance-evidence counts",
    )
    maintenance_summary.add_argument(
        "--stale-years",
        choices=(3, 5),
        type=int,
        default=3,
        help="activity cutoff in years (default: 3)",
    )
    maintenance_list = maintenance_commands.add_parser(
        "list",
        help="list tools by maintenance group or evidence status",
    )
    maintenance_list.add_argument(
        "--group",
        choices=("current", "attention"),
        default=None,
        help="top-level group (default: attention)",
    )
    maintenance_list.add_argument(
        "--status",
        choices=("current", "stale", "unknown", "archived"),
        help="filter by one evidence status",
    )
    maintenance_list.add_argument(
        "--stale-years",
        choices=(3, 5),
        type=int,
        default=3,
        help="activity cutoff in years (default: 3)",
    )
    maintenance_list.add_argument(
        "--limit",
        type=_positive_int,
        default=100,
        help="maximum rows to show (default: 100)",
    )

    collection = commands.add_parser(
        "collection",
        help="browse and apply reviewed security-tool collections",
    )
    collection_commands = collection.add_subparsers(
        dest="collection_command",
        required=True,
    )
    collection_commands.add_parser("list", help="list built-in collections")
    collection_show = collection_commands.add_parser("show", help="show one collection")
    collection_show.add_argument("name", help="built-in collection ID")
    collection_apply = collection_commands.add_parser(
        "apply",
        help="plan or install one built-in collection",
    )
    collection_apply.add_argument("name", help="built-in collection ID")
    collection_apply.add_argument(
        "--apply",
        action="store_true",
        help="install after review; plan-only by default",
    )
    return parser


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        raise BackendError("Confirmation requires a terminal; use --yes or --dry-run")
    answer = input(f"{prompt} [y/N] ").strip().casefold()
    return answer in {"y", "yes"}


def _normalize_global_options(argv: Sequence[str]) -> list[str]:
    values = list(argv)
    global_flags = {"--json", "--dry-run", "-y", "--yes"}
    hoisted: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(values):
        value = values[index]
        if value == "--":
            remaining.extend(values[index:])
            break
        if value in global_flags:
            hoisted.append(value)
        elif value == "--catalog":
            if index + 1 >= len(values):
                remaining.append(value)
            else:
                hoisted.extend((value, values[index + 1]))
                index += 1
        elif value.startswith("--catalog="):
            hoisted.append(value)
        else:
            remaining.append(value)
        index += 1
    return [*hoisted, *remaining]


def _subparser_action(parser: argparse.ArgumentParser):
    return next(
        (
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ),
        None,
    )


def _resolve_help_parser(
    parser: argparse.ArgumentParser,
    path: Sequence[str],
) -> argparse.ArgumentParser:
    current = parser
    for component in path:
        action = _subparser_action(current)
        if action is None or component not in action.choices:
            raise CatalogError(f"Unknown help topic: {' '.join(path)}")
        current = action.choices[component]
    return current


def _all_help_parsers(
    parser: argparse.ArgumentParser,
) -> list[tuple[str, argparse.ArgumentParser]]:
    result: list[tuple[str, argparse.ArgumentParser]] = []
    seen: set[int] = set()

    def visit(current: argparse.ArgumentParser, prefix: str) -> None:
        action = _subparser_action(current)
        if action is None:
            return
        for name, child in action.choices.items():
            identity = id(child)
            if identity in seen:
                continue
            seen.add(identity)
            path = f"{prefix} {name}".strip()
            result.append((path, child))
            visit(child, path)

    visit(parser, "")
    return result


def _print_command_help(
    parser: argparse.ArgumentParser,
    path: Sequence[str],
    *,
    show_all: bool = False,
) -> int:
    if show_all:
        parser.print_help()
        for topic, command_parser in _all_help_parsers(parser):
            print(f"\n{'=' * 78}\nblackforge {topic}\n{'=' * 78}")
            command_parser.print_help()
        return 0
    _resolve_help_parser(parser, path).print_help()
    return 0


def _display_tools(tools: Sequence, as_json: bool) -> None:
    payloads = [_tool_payload(tool) for tool in tools]
    if as_json:
        emit_json(payloads)
        return
    table(
        ["Name", "Version", "Source", "Category", "Description"],
        (
            (
                item["name"],
                item["version"],
                item["source"],
                item["category"],
                item["description"],
            )
            for item in payloads
        ),
    )


def _tool_payload(tool) -> dict[str, object]:
    if isinstance(tool, ArchTool):
        value = tool.to_dict()
        return {
            **value,
            "source": f"Arch/{tool.repository}",
            "category": ", ".join(tool.categories),
        }
    return {
        **tool.to_dict(),
        "source": "BlackArch",
    }


def _resolve_package_target(catalog: Catalog, reference: str) -> str:
    if reference.startswith("blackarch:"):
        reference = reference.removeprefix("blackarch:")
    if reference in catalog.by_name:
        return reference
    try:
        return resolve_arch_tool(reference).package_target
    except SourceError as exc:
        raise CatalogError(
            f"Unknown BlackArch or curated official Arch package: {reference}"
        ) from exc


def _resolve_package_targets(catalog: Catalog, references: Sequence[str]) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    for reference in references:
        target = _resolve_package_target(catalog, reference)
        if target not in seen:
            targets.append(target)
            seen.add(target)
    if not targets:
        raise CatalogError("At least one package is required")
    return targets


def _resolve_removal_names(catalog: Catalog, references: Sequence[str]) -> list[str]:
    names: list[str] = []
    for value in references:
        if (
            value.startswith(("arch:", "blackarch:"))
            or "/" in value
            or value in catalog.by_name
            or is_curated_arch_tool(value)
        ):
            name = _target_name(_resolve_package_target(catalog, value))
        else:
            name = validate_package_names([value])[0]
        if name not in names:
            names.append(name)
    return names


def _select_package_targets(args: argparse.Namespace, catalog: Catalog) -> list[str]:
    selectors = sum(
        (
            bool(args.names),
            bool(getattr(args, "category", None)),
            bool(getattr(args, "profile", None)),
        )
    )
    if selectors > 1:
        raise CatalogError("Choose package names, --category, or --profile; do not combine them")
    if args.names:
        return _resolve_package_targets(catalog, args.names)
    if getattr(args, "category", None):
        if args.category in catalog.categories:
            return [tool.name for tool in catalog.categories[args.category]]
        arch_tools = list_arch_tools(category=args.category)
        if arch_tools:
            return [tool.package_target for tool in arch_tools]
        raise CatalogError(f"Unknown category: {args.category}")
    if getattr(args, "profile", None):
        _, names = read_profile(args.profile)
        return _resolve_package_targets(catalog, names)
    raise CatalogError("Provide package names, --category, or --profile")


def _display_plan(plan, as_json: bool) -> None:
    if as_json:
        emit_json(plan.to_dict())
        return
    print(f"Operation: {plan.operation}")
    print(f"Packages: {len(plan.requested)} requested")
    print(f"Command: {command_preview(plan.command)}")
    if plan.dependencies:
        print("Resolved dependencies: " + ", ".join(plan.dependencies))
    if plan.conflicts:
        print("Conflicts: " + ", ".join(plan.conflicts))
    if plan.download_size_bytes is not None:
        print(f"Download: {_human_bytes(plan.download_size_bytes)}")
    if plan.installed_size_bytes is not None:
        print(f"Installed size: {_human_bytes(plan.installed_size_bytes)}")
    if plan.free_disk_bytes is not None:
        print(f"Free disk: {_human_bytes(plan.free_disk_bytes)}")
    for warning in plan.warnings:
        print(f"Warning: {warning}")


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _target_name(target: str) -> str:
    return target.split("/", 1)[-1]


def _target_ref(target: str) -> str:
    return (
        f"arch:{_target_name(target)}"
        if "/" in target
        else f"blackarch:{target}"
    )


def _operation_versions(
    installed: dict[str, str],
    targets: Sequence[str],
) -> dict[str, str | None]:
    return {
        _target_ref(target): installed.get(_target_name(target))
        for target in targets
    }


def _recorded_package_operation(
    backend: PacmanBackend,
    action: str,
    targets: Sequence[str],
    execute,
    *,
    retries: int = 0,
) -> int:
    if retries > 9:
        raise BackendError("--retries cannot exceed 9")
    before = backend.installed_packages()
    references = [_target_ref(target) for target in targets]
    journal = TransactionJournal()
    transaction = journal.begin(
        action,
        references,
        max_attempts=retries + 1,
    )
    current = transaction
    while True:
        try:
            result = execute()
        except BaseException as exc:
            failed = journal.mark_failed(current.transaction_id, exc)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if not failed.retryable:
                raise
            current = journal.resume(current.transaction_id)
            print(
                f"Retrying after a recognized {failed.retry_category} failure "
                f"(attempt {current.attempt}/{current.max_attempts})..."
            )
            continue
        if result.returncode == 0:
            journal.mark_completed(current.transaction_id)
            break
        detail = (result.stderr or result.stdout).strip() or (
            f"pacman exited with status {result.returncode}"
        )
        failed = journal.mark_failed(current.transaction_id, detail)
        if not failed.retryable:
            return result.returncode
        current = journal.resume(current.transaction_id)
        print(
            f"Retrying after a recognized {failed.retry_category} failure "
            f"(attempt {current.attempt}/{current.max_attempts})..."
        )
    try:
        after = backend.installed_packages()
    except BackendError as exc:
        print(
            "Warning: the package operation succeeded, but its final installed "
            f"versions could not be recorded: {exc}"
        )
        return 0
    try:
        HistoryStore().append(
            make_history_record(
                transaction.transaction_id,
                action,
                _operation_versions(before, targets),
                _operation_versions(after, targets),
            )
        )
    except HistoryError as exc:
        print(f"Warning: package operation succeeded but history was not saved: {exc}")
    return 0


def _run_install(args: argparse.Namespace, catalog: Catalog, backend: PacmanBackend) -> int:
    names = _select_package_targets(args, catalog)
    needs_blackarch = any("/" not in name for name in names)
    planning_backend = (
        backend
        if backend.supported and (not needs_blackarch or backend.repo_enabled)
        else None
    )
    plan = plan_install(names, backend=planning_backend, assume_yes=args.yes)
    if args.dry_run:
        setup_required = (
            getattr(args, "setup_repo", False)
            and any("/" not in name for name in names)
            and not backend.repo_enabled
        )
        if args.json:
            emit_json(
                {
                    **plan.to_dict(),
                    "blackarch_repository_setup_required": setup_required,
                }
            )
            return 0
        if setup_required:
            print("Would enable the official BlackArch repository first.")
        _display_plan(plan, False)
        return 0
    backend.require_supported()
    if needs_blackarch and not backend.repo_enabled:
        if getattr(args, "setup_repo", False):
            setup_args = argparse.Namespace(
                strap_sha256=None,
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
    plan = plan_install(names, backend=backend, assume_yes=args.yes)
    _display_plan(plan, False)
    if not args.yes and not _confirm(f"Install {len(names)} package(s) with pacman?"):
        print("Cancelled.")
        return 1
    return _recorded_package_operation(
        backend,
        "install",
        names,
        lambda: backend.install(names),
        retries=getattr(args, "retries", 0),
    )


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


def _show_tool_info(
    reference: str,
    catalog: Catalog,
    backend: PacmanBackend,
    as_json: bool,
) -> int:
    installed_version: str | None = None
    if backend.supported:
        installed_version = backend.installed_packages().get(
            reference.split("/", 1)[-1].split(":", 1)[-1]
        )
    blackarch_name = reference.removeprefix("blackarch:")
    if blackarch_name in catalog.by_name:
        tool = catalog.by_name[blackarch_name]
        evidence = load_bundled_maintenance().for_tool(tool.name)
        payload: dict[str, object] = {
            **tool.to_dict(),
            "source": "BlackArch",
            "repository": "blackarch",
            "package_target": tool.name,
            "installed_version": installed_version,
            "install_state": "installed" if installed_version else "not-installed",
            "maintenance": evidence.to_dict(),
            "maintenance_note": (
                "Upstream activity evidence is not a runtime compatibility test."
            ),
        }
    else:
        tool = resolve_arch_tool(reference)
        payload = {
            **tool.to_dict(),
            "source": tool.source_label,
            "installed_version": installed_version,
            "install_state": "installed" if installed_version else "not-installed",
            "maintenance": {
                "status": "current",
                "top_group": "current",
                "last_activity_at": None,
                "checked_at": bundled_arch_catalog().fetched_at,
                "evidence_url": tool.official_url,
                "evidence_kind": "official-arch-package-metadata",
                "confidence": "high",
                "note": (
                    "The package listing was current when the curated snapshot was "
                    "reviewed. This is not an upstream-activity or runtime test."
                ),
            },
        }
    if as_json:
        emit_json(payload)
        return 0
    maintenance = payload["maintenance"]
    assert isinstance(maintenance, dict)
    rows = [
        ("Name", payload["name"]),
        ("Source", payload["source"]),
        ("Repository", payload["repository"]),
        ("Version", payload["version"]),
        ("Installed", payload["installed_version"] or "no"),
        ("Category", payload.get("category") or ", ".join(payload.get("categories", []))),
        ("Maintenance", maintenance.get("status", "unknown")),
        ("Last activity", maintenance.get("last_activity_at") or "unknown"),
        ("Evidence", maintenance.get("evidence_url") or "-"),
        ("Website", payload.get("website") or "-"),
        ("Description", payload["description"]),
    ]
    table(["Field", "Value"], rows)
    return 0


def _repo_enable(args: argparse.Namespace, backend: PacmanBackend) -> int:
    if backend.repo_enabled:
        print("The [blackarch] repository is already configured.")
        return 0
    backend.require_supported()
    script, digest, sha1, checksum_matched = backend.download_strap(
        reviewed_sha256=args.strap_sha256
    )
    try:
        print(f"Downloaded official script: {script}")
        print(f"SHA-256: {digest}")
        checksum_state = "matched" if checksum_matched else "MISMATCH - exact SHA-256 approved"
        print(f"Official SHA-1 check: {sha1} ({checksum_state})")
        if not checksum_matched:
            print(
                "WARNING: The downloaded root setup script does not match the "
                "pinned BlackArch checksum."
            )
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


def _handle_plan(args: argparse.Namespace, backend: PacmanBackend) -> int:
    planning_backend = backend if backend.supported else None
    if args.plan_command == "install":
        value = plan_install(args.names, backend=planning_backend, assume_yes=args.yes)
    elif args.plan_command == "remove":
        value = plan_remove(
            args.names,
            backend=planning_backend,
            assume_yes=args.yes,
            purge=args.purge,
        )
    else:
        value = plan_upgrade(args.names, backend=planning_backend, assume_yes=args.yes)
    _display_plan(value, args.json)
    return 0


def _history_payload(record) -> dict[str, object]:
    return record.to_dict()


def _handle_history(args: argparse.Namespace, backend: PacmanBackend) -> int:
    store = HistoryStore()
    if args.history_command == "list":
        records = list(reversed(store.records()))[: args.limit]
        if args.json:
            emit_json([_history_payload(record) for record in records])
        else:
            table(
                ["Transaction", "When", "Action", "Outcome", "Packages"],
                (
                    (
                        record.transaction_id,
                        record.created_at,
                        record.action,
                        record.outcome,
                        len(record.packages),
                    )
                    for record in records
                ),
            )
        return 0
    record = store.get(args.transaction_id)
    if args.history_command == "show":
        if args.json:
            emit_json(_history_payload(record))
        else:
            table(
                ["Package", "Change", "Before", "After"],
                (
                    (
                        change.ref.qualified,
                        change.action,
                        change.before_version or "-",
                        change.after_version or "-",
                    )
                    for change in record.packages
                ),
            )
        return 0
    undo = plan_undo(record)
    if not args.apply or args.dry_run:
        if args.json:
            emit_json(undo.to_dict())
        else:
            table(
                ["Package", "Undo action", "Exact", "Reason"],
                (
                    (
                        step.ref.qualified,
                        step.action,
                        step.exact,
                        step.reason,
                    )
                    for step in undo.steps
                ),
            )
            print("\nPlan only: exact downgrades are never guessed.")
        return 0
    if not undo.automatic_execution_supported:
        raise HistoryError(
            "This transaction cannot be fully undone automatically. Review the "
            "plan; BlackForge will not apply a partial or guessed rollback."
        )
    removable = [
        step
        for step in undo.steps
        if step.action == "remove-newly-installed" and step.exact
    ]
    if not removable:
        raise HistoryError(
            "This transaction has no newly installed packages that can be safely removed"
        )
    backend.require_supported()
    installed = backend.installed_packages()
    changes = {change.ref.qualified: change for change in record.packages}
    targets: list[str] = []
    names: list[str] = []
    for step in removable:
        target = _ref_target(step.ref)
        name = _target_name(target)
        current_version = installed.get(name)
        if current_version is None:
            continue
        expected_version = changes[step.ref.qualified].after_version
        if current_version != expected_version:
            raise HistoryError(
                f"{step.ref.qualified} changed after transaction "
                f"{record.transaction_id}: recorded {expected_version}, "
                f"currently {current_version}. Refusing automatic removal."
            )
        targets.append(target)
        names.append(name)
    if not names:
        print("The newly installed packages are already absent; nothing to undo.")
        return 0
    value = plan_remove(names, backend=backend, assume_yes=args.yes)
    _display_plan(value, False)
    if not args.yes and not _confirm(
        f"Remove {len(names)} package(s) newly installed by this transaction?"
    ):
        print("Cancelled.")
        return 1
    return _recorded_package_operation(
        backend,
        "undo",
        targets,
        lambda: backend.remove(names),
    )


def _ref_target(ref: PackageRef, catalog: Catalog | None = None) -> str:
    if ref.source == "arch":
        return resolve_arch_tool(ref.name).package_target
    blackarch = catalog or bundled_catalog()
    if ref.name not in blackarch.by_name:
        raise CatalogError(
            f"{ref.qualified} is not present in the trusted BlackArch catalog"
        )
    return ref.name


def _handle_resume(args: argparse.Namespace, backend: PacmanBackend) -> int:
    journal = TransactionJournal()
    requested_id = getattr(args, "transaction_id", None)
    failed = (
        journal.get(requested_id)
        if requested_id
        else next(
            (
                record
                for record in reversed(journal.records())
                if record.status == "failed"
            ),
            None,
        )
    )
    if failed is None:
        raise TransactionError("No failed transaction is available to resume")
    if failed.status != "failed":
        raise TransactionError(
            f"Transaction {failed.transaction_id} is {failed.status}, not failed"
        )
    metadata = journal.resume_metadata(failed.transaction_id)
    if not args.apply or args.dry_run:
        if args.json:
            emit_json(metadata.to_dict())
        else:
            table(
                ["Field", "Value"],
                (
                    ("Transaction", metadata.transaction_id),
                    ("Can resume", metadata.can_resume),
                    ("Attempt", f"{metadata.attempt}/{metadata.max_attempts}"),
                    ("Remaining", len(metadata.remaining_packages)),
                    ("Reason", metadata.reason),
                ),
            )
        return 0 if metadata.can_resume else 3
    backend.require_supported()
    targets = [_ref_target(ref) for ref in metadata.remaining_packages]
    before = backend.installed_packages()
    pending = journal.resume(failed.transaction_id)
    try:
        if pending.action in {"install", "environment-import"}:
            result = backend.install(targets)
        elif pending.action in {"remove", "undo"}:
            result = backend.remove([_target_name(target) for target in targets])
        elif pending.action == "upgrade":
            result = backend.upgrade(targets)
        else:
            raise TransactionError(
                f"Automatic resume is unsupported for {pending.action!r}"
            )
    except BaseException as exc:
        journal.mark_failed(pending.transaction_id, exc)
        raise
    if result.returncode == 0:
        journal.mark_completed(pending.transaction_id)
        try:
            after = backend.installed_packages()
        except BackendError as exc:
            print(
                "Warning: the resumed package operation succeeded, but its final "
                f"installed versions could not be recorded: {exc}"
            )
        else:
            try:
                HistoryStore().append(
                    make_history_record(
                        pending.transaction_id,
                        pending.action,
                        _operation_versions(before, targets),
                        _operation_versions(after, targets),
                    )
                )
            except HistoryError as exc:
                print(
                    "Warning: resumed package operation succeeded but history was "
                    f"not saved: {exc}"
                )
        print(f"Transaction {pending.transaction_id} completed.")
        return 0
    detail = (result.stderr or result.stdout).strip() or (
        f"pacman exited with status {result.returncode}"
    )
    journal.mark_failed(
        pending.transaction_id,
        detail,
    )
    return result.returncode


def _handle_mirror(args: argparse.Namespace) -> int:
    path = args.path or DEFAULT_MIRRORLIST
    if args.mirror_command == "list":
        mirrors = list_mirrors(path)
        if args.json:
            emit_json([mirror.to_dict() for mirror in mirrors])
        else:
            table(
                ["Enabled", "Scheme", "Supported", "URL", "Reason"],
                (
                    (
                        mirror.enabled,
                        mirror.scheme,
                        mirror.supported,
                        mirror.url,
                        mirror.reason,
                    )
                    for mirror in mirrors
                ),
            )
        return 0
    if args.mirror_command in {"test", "recommend"}:
        mirrors = list_mirrors(path)
        timeout = float(getattr(args, "timeout", 5))
        results = test_mirrors(mirrors, timeout=timeout)
        if args.mirror_command == "recommend":
            best = recommend_mirror(results)
            if best is None:
                raise MirrorError("No responsive HTTPS BlackArch mirror was found")
            if args.json:
                emit_json(best.to_dict())
            else:
                print(
                    f"Recommended: {best.mirror.url} "
                    f"({best.latency_ms:.1f} ms)"
                )
            return 0
        if args.json:
            emit_json([result.to_dict() for result in results])
        else:
            table(
                ["Status", "Latency ms", "Enabled", "URL", "Error"],
                (
                    (
                        result.status,
                        result.latency_ms or "-",
                        result.mirror.enabled,
                        result.mirror.url,
                        result.error,
                    )
                    for result in results
                ),
            )
        return 0 if any(result.successful for result in results) else 3
    if args.dry_run:
        mirrors = list_mirrors(path)
        selected = next(
            (mirror for mirror in mirrors if mirror.url == args.url),
            None,
        )
        if selected is None:
            raise MirrorError("Selected mirror is not present in the mirror list")
        if selected.scheme != "https":
            raise MirrorError("Only HTTPS mirrors may be applied")
        would_change = not selected.enabled or any(
            mirror.enabled and mirror.url != selected.url
            for mirror in mirrors
        )
        payload = {
            "operation": "mirror-apply",
            "path": str(path),
            "selected_url": args.url,
            "would_change": would_change,
            "dry_run": True,
            "note": (
                "The mirror list was not changed. A backup is created only when "
                "the reviewed selection changes the file."
            ),
        }
        emit_json(payload) if args.json else table(
            ["Field", "Value"],
            payload.items(),
        )
        return 0
    approved = args.yes or _confirm(
        f"Select {args.url} in {path} and create a timestamped backup?"
    )
    if not approved:
        print("Cancelled.")
        return 1
    result = apply_mirror(
        path,
        args.url,
        approved=approved is True,
        expected_path=path,
    )
    if args.json:
        emit_json(result.to_dict())
    elif result.changed:
        print(f"Selected {result.selected_url}\nBackup: {result.backup}")
    else:
        print(f"{result.selected_url} is already selected; no file was changed.")
    return 0


def _display_update_report(report, as_json: bool) -> None:
    if as_json:
        emit_json(report.to_dict())
        return
    table(
        ["Check", "Count"],
        (
            ("Previous catalog", report.old_count),
            ("Live catalog", report.new_count),
            ("Added", len(report.added)),
            ("Removed", len(report.removed)),
            ("Version changes", len(report.changed)),
        ),
    )
    if report.added:
        print("Added: " + ", ".join(report.added[:20]))
    if report.removed:
        print("Removed: " + ", ".join(report.removed[:20]))


def _handle_updates(args: argparse.Namespace) -> int:
    if args.updates_command == "check":
        live = download_catalog(args.url)
        report = compare_catalogs(bundled_catalog().tools, live.tools)
        if not args.dry_run:
            save_report(report)
    else:
        report = read_report()
    _display_update_report(report, args.json)
    return 0


def _environment_current(
    catalog: Catalog,
    backend: PacmanBackend,
) -> dict[str, str]:
    installed = backend.installed_packages()
    values = {
        f"blackarch:{name}": version
        for name, version in installed.items()
        if name in catalog.by_name
    }
    values.update(
        {
            f"arch:{name}": installed[name]
            for name in bundled_arch_catalog().by_name
            if name in installed
        }
    )
    return values


def _handle_environment(
    args: argparse.Namespace,
    catalog: Catalog,
    backend: PacmanBackend,
) -> int:
    if args.environment_command == "export":
        backend.require_supported()
        current = _environment_current(catalog, backend)
        manifest = create_environment(
            args.path.stem or "blackforge-environment",
            current,
        )
        if not args.dry_run:
            manifest = export_environment(
                args.path,
                manifest.name,
                manifest.packages,
                created_at=manifest.created_at,
            )
        emit_json(manifest.to_dict()) if args.json else print(
            f"Would export {len(manifest.packages)} packages to {args.path}"
            if args.dry_run
            else f"Exported {len(manifest.packages)} packages to {args.path}"
        )
        return 0
    manifest = read_environment(args.path)
    for package in manifest.packages:
        _ref_target(package.ref, catalog)
    current = _environment_current(catalog, backend) if backend.supported else {}
    import_plan = plan_environment_import(manifest, current)
    if args.json and (not args.apply or args.dry_run):
        emit_json(import_plan.to_dict())
    elif not args.apply or args.dry_run:
        table(
            ["State", "Count"],
            (
                ("Install", len(import_plan.install)),
                ("Already satisfied", len(import_plan.satisfied)),
                ("Version drift", len(import_plan.version_drift)),
                ("Ignored extras", len(import_plan.ignored_extras)),
            ),
        )
        print(import_plan.note)
    if not args.apply or args.dry_run:
        return 0
    backend.require_supported()
    if (import_plan.install or import_plan.version_drift) and not args.allow_newer:
        raise EnvironmentFileError(
            "The environment requires package installation or has version drift. "
            "Exact rolling-release versions cannot be promised; review the plan "
            "and rerun with --allow-newer to install current signed versions."
        )
    packages = [item.ref for item in import_plan.install]
    if args.allow_newer:
        packages.extend(item.ref for item in import_plan.version_drift)
    targets = [_ref_target(ref, catalog) for ref in packages]
    if not targets:
        print("Environment is already satisfied.")
        return 0
    value = plan_install(targets, backend=backend, assume_yes=args.yes)
    _display_plan(value, False)
    if not args.yes and not _confirm(
        f"Install {len(targets)} package(s) from this environment?"
    ):
        print("Cancelled.")
        return 1
    return _recorded_package_operation(
        backend,
        "environment-import",
        targets,
        lambda: backend.install(targets),
    )


def _handle_maintenance(args: argparse.Namespace, catalog: Catalog) -> int:
    snapshot = load_bundled_maintenance(stale_years=args.stale_years, required=True)
    records = [
        (name, snapshot.for_tool(name))
        for name in catalog.by_name
    ]
    if args.maintenance_command == "summary":
        counts = {"current": 0, "stale": 0, "unknown": 0, "archived": 0}
        for _, evidence in records:
            counts[evidence.status.value] += 1
        payload = {
            "catalog_tools": len(records),
            "recently_maintained": counts["current"],
            "needs_attention": len(records) - counts["current"],
            **counts,
            "cutoff_years": args.stale_years,
            "generated_at": (
                snapshot.generated_at.isoformat()
                if snapshot.generated_at
                else None
            ),
        }
        if args.json:
            emit_json(payload)
        else:
            table(["Maintenance view", "Tools"], payload.items())
        return 0
    if args.status == "current" and args.group not in {None, "current"}:
        raise MaintenanceError(
            "Status 'current' belongs to --group current"
        )
    if (
        args.status in {"stale", "unknown", "archived"}
        and args.group not in {None, "attention"}
    ):
        raise MaintenanceError(
            f"Status {args.status!r} belongs to --group attention"
        )
    group = args.group
    if group is None:
        group = "current" if args.status == "current" else "attention"
    required_group = "current" if group == "current" else "needs-attention"
    selected = [
        (name, evidence)
        for name, evidence in records
        if evidence.top_group == required_group
        and (args.status is None or evidence.status.value == args.status)
    ][: args.limit]
    payload = [
        {
            "name": name,
            "category": catalog.by_name[name].category,
            **evidence.to_dict(),
        }
        for name, evidence in selected
    ]
    if args.json:
        emit_json(payload)
    else:
        table(
            ["Name", "Status", "Last activity", "Category", "Evidence"],
            (
                (
                    item["name"],
                    item["status"],
                    item["last_activity_at"] or "unknown",
                    item["category"],
                    item["evidence_url"] or "-",
                )
                for item in payload
            ),
        )
    return 0


def _handle_collection(
    args: argparse.Namespace,
    backend: PacmanBackend,
) -> int:
    if args.collection_command == "list":
        presets = list_presets()
        if args.json:
            emit_json([preset.to_dict() for preset in presets])
        else:
            table(
                ["ID", "Name", "Packages", "Description"],
                (
                    (
                        preset.id,
                        preset.name,
                        len(preset.packages),
                        preset.description,
                    )
                    for preset in presets
                ),
            )
        return 0
    preset = resolve_preset(args.name)
    packages = resolve_preset_packages(preset)
    if args.collection_command == "show":
        payload = {
            **preset.to_dict(),
            "resolved_packages": [package.to_dict() for package in packages],
        }
        if args.json:
            emit_json(payload)
        else:
            table(
                ["Source", "Repository", "Package", "Pacman target"],
                (
                    (
                        package.source,
                        package.repository,
                        package.name,
                        package.package_target,
                    )
                    for package in packages
                ),
            )
        return 0
    targets = [package.package_target for package in packages]
    planning_backend = backend if backend.supported else None
    value = plan_install(targets, backend=planning_backend, assume_yes=args.yes)
    _display_plan(value, args.json and (not args.apply or args.dry_run))
    if not args.apply or args.dry_run:
        return 0
    backend.require_supported()
    if any("/" not in target for target in targets) and not backend.repo_enabled:
        raise BackendError("Enable BlackArch first with `blackforge setup`")
    if not args.yes and not _confirm(f"Install collection {preset.name}?"):
        print("Cancelled.")
        return 1
    return _recorded_package_operation(
        backend,
        "install",
        targets,
        lambda: backend.install(targets),
    )


def run(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(_normalize_global_options(raw_argv))
    if not args.command:
        if sys.stdin.isatty():
            args.command = "interactive"
        else:
            parser.print_help()
            return 0

    if args.command == "help":
        return _print_command_help(parser, args.path, show_all=args.all)
    if args.command == "version":
        print(f"blackforge {__version__}")
        return 0

    backend = PacmanBackend(dry_run=args.dry_run, assume_yes=args.yes)

    if args.command in {"sync", "update-catalog"}:
        catalog = download_catalog(args.url)
        destination = args.output or default_cache_path()
        if not args.dry_run:
            catalog.write(destination)
        result = {"path": str(destination), "tools": len(catalog.tools), "source": catalog.source}
        if args.json:
            emit_json({**result, "saved": not args.dry_run})
        elif args.dry_run:
            print(
                f"Catalog is valid ({len(catalog.tools)} tools); "
                f"would save it to {destination}"
            )
        else:
            print(f"Synced {len(catalog.tools)} tools to {destination}")
        return 0

    if args.command == "plan":
        return _handle_plan(args, backend)
    if args.command == "history":
        return _handle_history(args, backend)
    if args.command == "resume":
        return _handle_resume(args, backend)
    if args.command == "mirror":
        return _handle_mirror(args)
    if args.command == "updates":
        return _handle_updates(args)
    if args.command == "self-update":
        release = check_latest()
        if not args.apply or args.dry_run:
            payload = release.to_dict()
            if args.json:
                emit_json(payload)
            else:
                table(["Field", "Value"], payload.items())
            return 0
        if not args.yes and not _confirm(
            f"Update BlackForge {__version__} to {release.version}?"
        ):
            print("Cancelled.")
            return 1
        print(apply_release(release))
        return 0
    if args.command == "collection":
        return _handle_collection(args, backend)
    if args.command == "setup":
        return _repo_enable(args, backend)
    if args.command == "repo":
        if args.repo_command == "status":
            value = {"enabled": backend.repo_enabled, "supported": backend.supported}
            emit_json(value) if args.json else print(
                "enabled" if value["enabled"] else "not enabled"
            )
            return 0 if value["enabled"] else 2
        return _repo_enable(args, backend)
    if args.command == "completion":
        print(completion_script(args.shell), end="")
        return 0

    catalog = load_catalog(args.catalog)
    if args.command == "env":
        return _handle_environment(args, catalog, backend)
    if args.command == "maintenance":
        return _handle_maintenance(args, catalog)
    if args.command == "tui":
        selected = run_tui([*catalog.tools, *bundled_arch_catalog().tools])
        if not selected:
            print("No packages selected.")
            return 0
        targets = _resolve_package_targets(catalog, selected)
        value = plan_install(
            targets,
            backend=backend if backend.supported else None,
            assume_yes=args.yes,
        )
        _display_plan(value, args.json)
        print("Review the plan, then run: blackforge install " + " ".join(selected))
        return 0
    if args.command == "list":
        tools: list = []
        if args.source in {"all", "blackarch"}:
            if args.category and args.category not in catalog.categories:
                blackarch_tools = []
            else:
                blackarch_tools = (
                    catalog.categories[args.category]
                    if args.category
                    else catalog.tools
                )
            tools.extend(blackarch_tools)
        if args.source in {"all", "arch"}:
            try:
                tools.extend(list_arch_tools(category=args.category))
            except SourceError:
                if args.source == "arch":
                    raise
        if args.category and not tools:
            raise CatalogError(f"Unknown or empty category: {args.category}")
        _display_tools(tools[: args.limit], args.json)
        return 0
    if args.command == "names":
        prefix = args.prefix.casefold()
        if args.categories:
            values = [
                *catalog.categories,
                *bundled_arch_catalog().categories,
            ]
        else:
            tools = (
                catalog.categories.get(args.category, [])
                if args.category
                else catalog.tools
            )
            arch_tools = (
                list_arch_tools(category=args.category)
                if args.category in bundled_arch_catalog().categories
                else ([] if args.category else list_arch_tools())
            )
            if (
                args.category
                and args.category not in catalog.categories
                and args.category not in bundled_arch_catalog().categories
            ):
                raise CatalogError(f"Unknown category: {args.category}")
            values = [tool.name for tool in tools]
            values.extend(tool.name for tool in arch_tools)
        for value in values:
            if value.casefold().startswith(prefix):
                print(value)
        return 0
    if args.command == "search":
        query = " ".join(args.query)
        tools = []
        if args.source in {"all", "blackarch"} and (
            not args.category or args.category in catalog.categories
        ):
            tools.extend(
                catalog.search(query, category=args.category, limit=args.limit)
            )
        if args.source in {"all", "arch"} and (
            not args.category or args.category in bundled_arch_catalog().categories
        ):
            tools.extend(list_arch_tools(query=query, category=args.category))
        tools.sort(
            key=lambda tool: (
                0 if tool.name.casefold() == query.casefold() else 1,
                tool.name.casefold(),
            )
        )
        tools = tools[: args.limit]
        if args.category and not tools and (
            args.category not in catalog.categories
            and args.category not in bundled_arch_catalog().categories
        ):
            raise CatalogError(f"Unknown category: {args.category}")
        _display_tools(tools, args.json)
        return 0 if tools else 1
    if args.command in {"show", "info"}:
        return _show_tool_info(args.name, catalog, backend, args.json)
    if args.command == "categories":
        values: list[dict[str, object]] = []
        if args.source in {"all", "blackarch"}:
            values.extend(
                {
                    "source": "BlackArch",
                    "category": name,
                    "tools": len(tools),
                }
                for name, tools in catalog.categories.items()
            )
        if args.source in {"all", "arch"}:
            values.extend(
                {
                    "source": "Arch",
                    "category": name,
                    "tools": len(tools),
                }
                for name, tools in bundled_arch_catalog().categories.items()
            )
        if args.json:
            emit_json(values)
        else:
            table(
                ["Source", "Category", "Tools"],
                (
                    (item["source"], item["category"], item["tools"])
                    for item in values
                ),
            )
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
        if args.output and not args.dry_run:
            atomic_write_json(args.output, payload)
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
        return audit.exit_code
    if args.command in {"install", "get", "add"}:
        return _run_install(args, catalog, backend)
    if args.command in {"remove", "rm", "uninstall"}:
        names = _resolve_removal_names(catalog, args.names)
        plan = plan_remove(
            names,
            backend=backend if backend.supported else None,
            assume_yes=args.yes,
            purge=args.purge,
        )
        if args.dry_run:
            _display_plan(plan, args.json)
            return 0
        backend.require_supported()
        installed = backend.installed_packages()
        missing = [name for name in names if name not in installed]
        if missing:
            raise BackendError("Package is not installed: " + ", ".join(missing))
        _display_plan(plan, False)
        if not args.yes and not _confirm(f"Remove {len(names)} package(s)?"):
            print("Cancelled.")
            return 1
        return _recorded_package_operation(
            backend,
            "remove",
            names,
            lambda: backend.remove(names, purge=args.purge),
        )
    if args.command == "upgrade":
        targets = (
            _resolve_package_targets(catalog, args.names)
            if args.names
            else []
        )
        plan = plan_upgrade(
            targets,
            backend=backend if backend.supported else None,
            assume_yes=args.yes,
        )
        if args.dry_run:
            _display_plan(plan, args.json)
            return 0
        backend.require_supported()
        _display_plan(plan, False)
        if not args.yes and not _confirm(
            "Upgrade selected packages?" if args.names else "Run a full system upgrade?"
        ):
            print("Cancelled.")
            return 1
        if not targets:
            return backend.upgrade().returncode
        return _recorded_package_operation(
            backend,
            "upgrade",
            targets,
            lambda: backend.upgrade(targets),
        )
    if args.command == "profile":
        if args.profile_command == "create":
            targets = _resolve_package_targets(catalog, args.packages)
            if not args.dry_run:
                write_profile(args.path, args.name or args.path.stem, targets)
            print(
                f"Would save {len(targets)} packages to {args.path}"
                if args.dry_run
                else f"Saved {len(targets)} packages to {args.path}"
            )
            return 0
        name, packages = read_profile(args.path)
        targets = _resolve_package_targets(catalog, packages)
        if args.profile_command == "show":
            payload = {"name": name, "packages": targets}
            if args.json:
                emit_json(payload)
            else:
                table(
                    ["Profile", "Package target"],
                    ((name, target) for target in targets),
                )
            return 0
        install_args = argparse.Namespace(**vars(args))
        install_args.names = []
        install_args.category = None
        install_args.profile = args.path
        install_args.setup_repo = False
        return _run_install(install_args, catalog, backend)
    if args.command == "export":
        if not args.dry_run:
            if args.format == "json":
                catalog.write(args.path)
            else:
                handle = io.StringIO(newline="")
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["name", "version", "description", "category", "website"],
                )
                writer.writeheader()
                writer.writerows(tool.to_dict() for tool in catalog.tools)
                atomic_write_text(args.path, handle.getvalue())
        print(
            f"Would export {len(catalog.tools)} tools to {args.path}"
            if args.dry_run
            else f"Exported {len(catalog.tools)} tools to {args.path}"
        )
        return 0
    if args.command == "interactive":
        return _interactive(catalog, backend, args)
    parser.error("Unknown command")
    return 2


def main() -> None:
    try:
        raise SystemExit(run())
    except (
        BackendError,
        CatalogError,
        EnvironmentFileError,
        HistoryError,
        MaintenanceError,
        MirrorError,
        PlannerError,
        PresetError,
        ProfileError,
        RepositoryError,
        SelfUpdateError,
        SourceError,
        TransactionError,
        TuiError,
        UpdateError,
    ) as exc:
        error(str(exc))
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        error("cancelled")
        raise SystemExit(130) from None
    except BrokenPipeError:
        try:
            sys.stdout.close()
        finally:
            raise SystemExit(0) from None
    except OSError as exc:
        error(f"operating system error: {exc}")
        raise SystemExit(2) from exc
