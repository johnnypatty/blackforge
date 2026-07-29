from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class StateLockError(RuntimeError):
    pass


def _lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_state_lock(path: Path) -> Iterator[None]:
    """Serialize a state-file read/modify/write cycle across processes."""

    lock_path = path.with_name(f".{path.name}.lock")
    handle: BinaryIO | None = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        if os.name == "nt" and handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        _lock(handle)
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise StateLockError(f"Unable to lock state file {path}: {exc}") from exc
    assert handle is not None
    try:
        yield
    finally:
        try:
            _unlock(handle)
        finally:
            handle.close()
