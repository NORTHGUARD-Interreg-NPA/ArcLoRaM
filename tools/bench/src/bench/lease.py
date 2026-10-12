"""Board leases: a command that works on a board holds it, so two sessions never share one (ADR-0003).

A lease is an advisory file lock (flock) on `<dir>/<board id>.lease`, so the operating system
releases it when the holding process ends, however it ends: nothing goes stale and nobody
bookkeeps. Taking is never blocking: a board held by another session is refused at once, with who
holds it, and the caller picks other boards or waits.

- exclusive: flash, reset, read a UID over SWD. One holder; its description is written in the file.
- shared: watch a board's trace in a run. Any number of watchers, but no flasher while watched.

Reading a board's trace is never leased: the capture is shared and read-only.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class Held(Exception):
    """Boards another session holds: board id -> who holds it."""

    def __init__(self, busy: dict[str, str]) -> None:
        self.busy = busy
        super().__init__("; ".join(f"{board} is held by {who}" for board, who in busy.items()))


class Leases:
    #: What a refusal says of a holder that left no description: a watcher, or a session that just took the file.
    UNKNOWN_HOLDER = "another session (watching it, or just taking it)"

    def __init__(self, directory: Path | str, worktree: str, command: str) -> None:
        self.dir = Path(directory)
        self.worktree = worktree
        self.command = command
        self._mine: set[str] = set()      # boards this object holds exclusively: taking them again is a no-op

    def _path(self, board: str) -> Path:
        if not _ID_RE.match(board):
            raise ValueError(f"not a board id: {board!r}")
        return self.dir / f"{board}.lease"

    def _describe(self, path: Path) -> str:
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            return (f"{info['worktree']}: {info['command']} (pid {info['pid']}, since "
                    f"{datetime.fromisoformat(info['since']):%H:%M:%S} UTC)")
        except (OSError, ValueError, KeyError):
            return self.UNKNOWN_HOLDER

    def _lock(self, fd: int, mode: int) -> bool:
        # A reader probing the board holds the lock for a moment: ask a few times before refusing.
        for attempt in range(4):
            try:
                fcntl.flock(fd, mode | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                if attempt < 3:
                    time.sleep(0.02)
        return False

    @contextmanager
    def hold(self, exclusive: Iterable[str] = (), shared: Iterable[str] = ()) -> Iterator[None]:
        """Take every board or none: raises Held with all the boards that are not free."""
        exclusive = set(exclusive) - self._mine
        shared = set(shared) - exclusive - self._mine
        self.dir.mkdir(parents=True, exist_ok=True)
        taken: list[tuple[str, int, bool]] = []
        busy: dict[str, str] = {}
        try:
            for board in sorted(exclusive | shared):
                path = self._path(board)
                fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
                is_exclusive = board in exclusive
                if not self._lock(fd, fcntl.LOCK_EX if is_exclusive else fcntl.LOCK_SH):
                    busy[board] = self._describe(path)
                    os.close(fd)
                    continue
                taken.append((board, fd, is_exclusive))
                if is_exclusive:
                    info = {"worktree": self.worktree, "command": self.command, "pid": os.getpid(),
                            "since": datetime.now(timezone.utc).isoformat()}
                    os.ftruncate(fd, 0)
                    os.write(fd, json.dumps(info).encode("utf-8"))
            if busy:
                raise Held(busy)
            self._mine |= {board for board, _, ex in taken if ex}
            yield
        finally:
            for board, fd, is_exclusive in taken:
                if is_exclusive:
                    os.ftruncate(fd, 0)
                    self._mine.discard(board)
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def try_exclusive(self, stack: ExitStack, board: str) -> bool:
        """Take one board for as long as `stack` stays open; False when another session holds it."""
        try:
            stack.enter_context(self.hold(exclusive=[board]))
        except Held:
            return False
        return True

    def exclusive_holders(self, boards: Iterable[str]) -> dict[str, str]:
        """Like `holders`, but only those held exclusively (they leave a description): a board that is
        merely watched, or a channel slot held as another channel's guard, is not listed."""
        return {board: who for board, who in self.holders(boards).items() if who != self.UNKNOWN_HOLDER}

    def holders(self, boards: Iterable[str]) -> dict[str, str]:
        """Which of these boards another session holds now, and who: looking takes nothing."""
        held: dict[str, str] = {}
        for board in boards:
            path = self._path(board)
            if not path.exists():
                continue
            fd = os.open(path, os.O_RDWR)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    held[board] = self._describe(path)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        return held
