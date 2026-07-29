from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_MIRRORLIST = Path("/etc/pacman.d/blackarch-mirrorlist")
MAX_MIRRORLIST_BYTES = 2 * 1024 * 1024
_SERVER_LINE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<comment>#[ \t]*)?"
    r"Server[ \t]*=[ \t]*(?P<url>\S+)(?P<trailing>[ \t]*(?:#.*)?)$",
    re.IGNORECASE,
)


class MirrorError(RuntimeError):
    """Raised when a mirror list or mirror selection is unsafe."""


@dataclass(frozen=True, slots=True)
class Mirror:
    url: str
    enabled: bool
    line_number: int
    scheme: str
    supported: bool
    reason: str = ""
    section: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MirrorTest:
    mirror: Mirror
    status: str
    latency_ms: float | None = None
    http_status: int | None = None
    tested_url: str = ""
    final_url: str = ""
    error: str = ""

    @property
    def successful(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mirror": self.mirror.to_dict(),
            "status": self.status,
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "tested_url": self.tested_url,
            "final_url": self.final_url,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class MirrorApplyResult:
    path: Path
    backup: Path | None
    selected_url: str
    changed: bool
    applied_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "backup": str(self.backup) if self.backup else None,
            "selected_url": self.selected_url,
            "changed": self.changed,
            "applied_at": self.applied_at,
        }


def _section_from_comment(line: str) -> str:
    text = line.strip()
    if not text.startswith("#"):
        return ""
    text = text.lstrip("#").strip()
    if not text or set(text) <= {"-", "=", "*"}:
        return ""
    if "mirrorlist" in text.casefold():
        return ""
    return text


def parse_mirrorlist(
    text: str,
    *,
    supported_schemes: Iterable[str] = ("https",),
) -> tuple[Mirror, ...]:
    """Parse enabled and commented Server entries without discarding failures."""

    allowed = {scheme.casefold() for scheme in supported_schemes}
    mirrors: list[Mirror] = []
    section = ""
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _SERVER_LINE.fullmatch(line)
        if not match:
            possible_section = _section_from_comment(line)
            if possible_section:
                section = possible_section
            continue
        url = match.group("url")
        scheme = urlsplit(url).scheme.casefold()
        if not scheme:
            supported = False
            reason = "mirror URL has no scheme"
        elif scheme not in allowed:
            supported = False
            allowed_text = ", ".join(sorted(allowed)) or "none"
            reason = (
                f"unsupported scheme {scheme!r}; allowed by default: {allowed_text}"
            )
        else:
            supported = True
            reason = ""
        mirrors.append(
            Mirror(
                url=url,
                enabled=match.group("comment") is None,
                line_number=line_number,
                scheme=scheme,
                supported=supported,
                reason=reason,
                section=section,
            )
        )
    return tuple(mirrors)


def read_mirrorlist(
    path: Path = DEFAULT_MIRRORLIST,
    *,
    supported_schemes: Iterable[str] = ("https",),
) -> tuple[Mirror, ...]:
    try:
        size = path.stat().st_size
        if size > MAX_MIRRORLIST_BYTES:
            raise MirrorError(
                f"Mirror list exceeds {MAX_MIRRORLIST_BYTES} bytes: {path}"
            )
        text = path.read_text(encoding="utf-8")
    except MirrorError:
        raise
    except (OSError, UnicodeError) as exc:
        raise MirrorError(f"Unable to read mirror list {path}: {exc}") from exc
    return parse_mirrorlist(text, supported_schemes=supported_schemes)


def list_mirrors(
    path: Path = DEFAULT_MIRRORLIST,
    *,
    enabled_only: bool = False,
    supported_schemes: Iterable[str] = ("https",),
) -> tuple[Mirror, ...]:
    mirrors = read_mirrorlist(path, supported_schemes=supported_schemes)
    if enabled_only:
        return tuple(mirror for mirror in mirrors if mirror.enabled)
    return mirrors


def _probe_url(url: str) -> str:
    expanded = url.replace("$repo", "blackarch").replace("$arch", "x86_64")
    if expanded.rstrip("/").endswith(".db"):
        return expanded
    return expanded.rstrip("/") + "/blackarch.db"


def _open(
    opener: Callable[..., Any] | Any,
    request: urllib.request.Request,
    timeout: float,
) -> Any:
    method = getattr(opener, "open", None)
    if callable(method):
        return method(request, timeout=timeout)
    if callable(opener):
        return opener(request, timeout=timeout)
    raise TypeError("opener must be callable or provide open()")


def _request_once(
    url: str,
    *,
    opener: Callable[..., Any] | Any,
    timeout: float,
    method: str,
) -> tuple[int, str]:
    headers = {"User-Agent": "BlackForge mirror test"}
    if method == "GET":
        headers["Range"] = "bytes=0-0"
    request = urllib.request.Request(url, headers=headers, method=method)
    response = _open(opener, request, timeout)
    try:
        status = int(
            getattr(response, "status", None)
            or getattr(response, "code", None)
            or response.getcode()
        )
        final_url = (
            response.geturl() if callable(getattr(response, "geturl", None)) else url
        )
        return status, str(final_url)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def probe_mirror(
    mirror: Mirror,
    *,
    opener: Callable[..., Any] | Any = urllib.request.urlopen,
    timeout: float = 5.0,
    allow_insecure: bool = False,
    clock: Callable[[], float] = time.perf_counter,
) -> MirrorTest:
    if mirror.scheme != "https" and not allow_insecure:
        return MirrorTest(
            mirror=mirror,
            status="unsupported",
            error=mirror.reason or "only HTTPS mirrors are tested by default",
        )
    if mirror.scheme not in {"https", "http"}:
        return MirrorTest(
            mirror=mirror,
            status="unsupported",
            error=f"scheme {mirror.scheme!r} cannot be tested",
        )
    tested_url = _probe_url(mirror.url)
    started = clock()
    try:
        try:
            status, final_url = _request_once(
                tested_url,
                opener=opener,
                timeout=timeout,
                method="HEAD",
            )
        except urllib.error.HTTPError as exc:
            if exc.code not in {405, 501}:
                raise
            status, final_url = _request_once(
                tested_url,
                opener=opener,
                timeout=timeout,
                method="GET",
            )
        latency_ms = max(0.0, (clock() - started) * 1000)
        final_scheme = urlsplit(final_url).scheme.casefold()
        if final_scheme != "https" and not allow_insecure:
            return MirrorTest(
                mirror=mirror,
                status="unsupported",
                latency_ms=round(latency_ms, 3),
                http_status=status,
                tested_url=tested_url,
                final_url=final_url,
                error="mirror redirected away from HTTPS",
            )
        if not 200 <= status < 400:
            return MirrorTest(
                mirror=mirror,
                status="unreachable",
                latency_ms=round(latency_ms, 3),
                http_status=status,
                tested_url=tested_url,
                final_url=final_url,
                error=f"unexpected HTTP status {status}",
            )
        return MirrorTest(
            mirror=mirror,
            status="ok",
            latency_ms=round(latency_ms, 3),
            http_status=status,
            tested_url=tested_url,
            final_url=final_url,
        )
    except (OSError, TypeError, ValueError, urllib.error.URLError) as exc:
        latency_ms = max(0.0, (clock() - started) * 1000)
        return MirrorTest(
            mirror=mirror,
            status="unreachable",
            latency_ms=round(latency_ms, 3),
            tested_url=tested_url,
            error=str(exc),
        )


def test_mirrors(
    mirrors: Sequence[Mirror],
    *,
    opener: Callable[..., Any] | Any = urllib.request.urlopen,
    timeout: float = 5.0,
    allow_insecure: bool = False,
    max_workers: int = 8,
) -> tuple[MirrorTest, ...]:
    if max_workers < 1:
        raise MirrorError("max_workers must be at least 1")
    results: list[MirrorTest | None] = [None] * len(mirrors)
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(mirrors)))) as pool:
        future_indexes = {
            pool.submit(
                probe_mirror,
                mirror,
                opener=opener,
                timeout=timeout,
                allow_insecure=allow_insecure,
            ): index
            for index, mirror in enumerate(mirrors)
        }
        for future in as_completed(future_indexes):
            results[future_indexes[future]] = future.result()
    return tuple(result for result in results if result is not None)


def recommend_mirror(
    results: Sequence[MirrorTest],
    *,
    allow_insecure: bool = False,
) -> MirrorTest | None:
    candidates = [
        result
        for result in results
        if result.successful
        and (allow_insecure or result.mirror.scheme == "https")
        and result.latency_ms is not None
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda result: (
            result.latency_ms if result.latency_ms is not None else float("inf"),
            result.mirror.line_number,
        ),
    )


def _validated_target(path: Path, expected_path: Path) -> Path:
    if path.name != "blackarch-mirrorlist":
        raise MirrorError(
            "Refusing to modify a file not named blackarch-mirrorlist"
        )
    if path.is_symlink():
        raise MirrorError("Refusing to replace a symbolic-link mirror list")
    try:
        resolved = path.resolve(strict=True)
        expected = expected_path.resolve(strict=True)
    except OSError as exc:
        raise MirrorError(f"Unable to resolve mirror-list path: {exc}") from exc
    if resolved != expected:
        raise MirrorError(
            f"Refusing unexpected mirror list {resolved}; expected exactly {expected}"
        )
    if not resolved.is_file():
        raise MirrorError(f"Mirror list is not a regular file: {resolved}")
    if resolved.stat().st_size > MAX_MIRRORLIST_BYTES:
        raise MirrorError("Mirror list is unexpectedly large")
    return resolved


def _timestamp(now: Callable[[], datetime] | None) -> tuple[str, str]:
    moment = now() if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return (
        moment.strftime("%Y%m%dT%H%M%S.%fZ"),
        moment.replace(microsecond=0).isoformat(),
    )


def _unique_backup_path(path: Path, timestamp: str) -> Path:
    base = path.with_name(f"{path.name}.bak.{timestamp}")
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{base.name}.{suffix}")
        suffix += 1
    return candidate


def _atomic_write(
    destination: Path,
    data: bytes,
    *,
    mode_from: os.stat_result,
) -> None:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(mode_from.st_mode))
        if hasattr(os, "chown"):
            try:
                os.chown(temporary, mode_from.st_uid, mode_from.st_gid)
            except (AttributeError, PermissionError):
                pass
        os.replace(temporary, destination)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _backup_atomic(path: Path, data: bytes, timestamp: str) -> Path:
    backup = _unique_backup_path(path, timestamp)
    _atomic_write(backup, data, mode_from=path.stat())
    try:
        shutil.copystat(path, backup, follow_symlinks=False)
    except OSError:
        pass
    return backup


def _select_mirror(text: str, selected_url: str) -> str:
    lines = text.splitlines(keepends=True)
    matches = 0
    output: list[str] = []
    for line in lines:
        ending = ""
        content = line
        if content.endswith("\r\n"):
            content, ending = content[:-2], "\r\n"
        elif content.endswith(("\n", "\r")):
            content, ending = content[:-1], content[-1:]
        match = _SERVER_LINE.fullmatch(content)
        if not match:
            output.append(line)
            continue
        url = match.group("url")
        indent = match.group("indent")
        trailing = match.group("trailing")
        if url == selected_url and matches == 0:
            matches += 1
            output.append(f"{indent}Server = {url}{trailing}{ending}")
        else:
            if url == selected_url:
                matches += 1
            output.append(f"{indent}# Server = {url}{trailing}{ending}")
    if matches == 0:
        raise MirrorError("Selected mirror is not present in the mirror list")
    return "".join(output)


def _siglevel_lines(text: str) -> tuple[str, ...]:
    return tuple(
        line
        for line in text.splitlines()
        if line.lstrip().casefold().startswith("siglevel")
    )


def apply_mirror(
    path: Path,
    selected_url: str,
    *,
    approved: bool,
    expected_path: Path = DEFAULT_MIRRORLIST,
    allow_insecure: bool = False,
    now: Callable[[], datetime] | None = None,
) -> MirrorApplyResult:
    """Atomically enable one listed mirror after explicit caller approval."""

    if approved is not True:
        raise MirrorError("Explicit approval is required before changing mirrors")
    target = _validated_target(path, expected_path)
    try:
        original_bytes = target.read_bytes()
        original = original_bytes.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise MirrorError(f"Unable to read mirror list {target}: {exc}") from exc

    mirrors = parse_mirrorlist(
        original,
        supported_schemes=("https", "http") if allow_insecure else ("https",),
    )
    selected = next((mirror for mirror in mirrors if mirror.url == selected_url), None)
    if selected is None:
        raise MirrorError("Selected mirror is not present in the mirror list")
    if selected.scheme != "https" and not allow_insecure:
        raise MirrorError("Only HTTPS mirrors may be applied by default")
    if selected.scheme not in {"https", "http"}:
        raise MirrorError(f"Unsupported mirror scheme: {selected.scheme!r}")

    updated = _select_mirror(original, selected_url)
    if _siglevel_lines(updated) != _siglevel_lines(original):
        raise MirrorError("Refusing a mirror update that changes SigLevel")

    timestamp, applied_at = _timestamp(now)
    if updated == original:
        return MirrorApplyResult(
            path=target,
            backup=None,
            selected_url=selected_url,
            changed=False,
            applied_at=applied_at,
        )
    try:
        backup = _backup_atomic(target, original_bytes, timestamp)
        _atomic_write(target, updated.encode("utf-8"), mode_from=target.stat())
    except OSError as exc:
        raise MirrorError(f"Unable to apply mirror selection: {exc}") from exc
    return MirrorApplyResult(
        path=target,
        backup=backup,
        selected_url=selected_url,
        changed=updated != original,
        applied_at=applied_at,
    )
