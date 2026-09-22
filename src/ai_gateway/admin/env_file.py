"""Upserts KEY=value lines in a dotenv-style file. Used only by the admin
UI to store account secrets -- nothing else in this project reads or
writes .env directly; everything else reads os.environ, which is normally
populated by whatever launched the process (e.g. `uv run --env-file`).
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


def _backup(env_path: Path) -> None:
    """Same timestamped-backup mechanism as ConfigWriter, applied to
    .env writes -- deleting or rotating an account key is otherwise
    unrecoverable once done.
    """
    if not env_path.exists():
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%f")
    backup_path = env_path.with_name(f"{env_path.name}.{timestamp}.bak")
    shutil.copy2(env_path, backup_path)


def backup_env_now(env_path: Path) -> None:
    """Manual, on-demand backup with no accompanying write -- the .env
    counterpart to ConfigWriter.backup_now().
    """
    _backup(env_path)


def upsert_env_var(env_path: Path, key: str, value: str) -> None:
    """Set KEY=value in the given .env file, replacing an existing line for
    that key or appending a new one, and also set it in the current
    process's os.environ so it's usable immediately without a restart.
    """
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    _backup(env_path)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def remove_env_var(env_path: Path, key: str) -> None:
    """Remove the KEY=... line for `key` from the given .env file (if
    present) and unset it from the current process's os.environ. Used only
    for account *deletion* -- rename/rotate deliberately keep old lines
    around, since those aren't destructive actions.
    """
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        remaining = [
            line
            for line in lines
            if not (line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="))
        ]
        if len(remaining) != len(lines):
            _backup(env_path)
            env_path.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")
    os.environ.pop(key, None)
