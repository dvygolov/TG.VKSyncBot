from __future__ import annotations

import os
import signal
import subprocess
import time
from collections import defaultdict, deque
from typing import Any

_PLAYWRIGHT_MARKERS = (
    "ms-playwright",
    "chromium_headless_shell",
    "chrome-linux",
)


def _is_playwright_command(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in _PLAYWRIGHT_MARKERS)


def _list_processes() -> list[tuple[int, int, str]]:
    if os.name != "posix":
        return []
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,args="],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return []

    result: list[tuple[int, int, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        result.append((pid, ppid, parts[2]))
    return result


def snapshot_playwright_pids() -> set[int]:
    return {pid for pid, _, command in _list_processes() if _is_playwright_command(command)}


def _pid_exists(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def _expand_process_tree(pids: set[int]) -> set[int]:
    roots = {pid for pid in pids if pid > 1}
    if not roots:
        return set()

    children: dict[int, list[int]] = defaultdict(list)
    for pid, ppid, _ in _list_processes():
        children[ppid].append(pid)

    expanded = set(roots)
    queue: deque[int] = deque(roots)
    while queue:
        current = queue.popleft()
        for child in children.get(current, []):
            if child in expanded:
                continue
            expanded.add(child)
            queue.append(child)
    return expanded


def _playwright_descendants(root_pid: int) -> set[int]:
    processes = _list_processes()
    if not processes:
        return set()

    children: dict[int, list[int]] = defaultdict(list)
    commands: dict[int, str] = {}
    for pid, ppid, command in processes:
        children[ppid].append(pid)
        commands[pid] = command

    descendants: set[int] = set()
    queue: deque[int] = deque([root_pid])
    while queue:
        current = queue.popleft()
        for child in children.get(current, []):
            if child in descendants:
                continue
            descendants.add(child)
            queue.append(child)

    return {pid for pid in descendants if _is_playwright_command(commands.get(pid, ""))}


def kill_process_trees(pids: set[int], grace_sec: float = 2.0) -> list[int]:
    targets = _expand_process_tree(pids)
    if not targets:
        return []

    if os.name == "nt":
        for pid in sorted(targets):
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
            except Exception:
                continue
        return []

    for pid in sorted(targets):
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            continue

    deadline = time.monotonic() + max(grace_sec, 0.1)
    alive = {pid for pid in targets if _pid_exists(pid)}
    while alive and time.monotonic() < deadline:
        time.sleep(0.1)
        alive = {pid for pid in alive if _pid_exists(pid)}

    if alive:
        for pid in sorted(alive):
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                continue
        time.sleep(0.1)

    return sorted(pid for pid in targets if _pid_exists(pid))


def close_playwright_objects(*, page: Any = None, context: Any = None, browser: Any = None) -> None:
    for obj in (page, context, browser):
        if obj is None:
            continue
        try:
            obj.close()
        except Exception:
            continue


class PlaywrightProcessGuard:
    def __init__(self) -> None:
        self._owner_pid = os.getpid()
        self._baseline = snapshot_playwright_pids()
        self._tracked: set[int] = set()

    def mark_spawned(self) -> None:
        descendants = _playwright_descendants(self._owner_pid)
        if descendants:
            self._tracked.update(descendants)
            return
        current = snapshot_playwright_pids()
        self._tracked.update(pid for pid in current if pid not in self._baseline)

    def cleanup(self, grace_sec: float = 2.0) -> list[int]:
        self.mark_spawned()
        survivors = kill_process_trees(self._tracked, grace_sec=grace_sec)
        self._tracked.clear()
        return survivors
