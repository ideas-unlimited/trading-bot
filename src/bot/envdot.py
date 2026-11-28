"""Minimal ``.env`` loader.

Reads ``KEY=VALUE`` lines from a dotfile and exports any that are not already
in the environment.  Exists so API keys can live in ``.env`` (git-ignored)
instead of being typed into the shell or pasted into chat.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_for(env_name: str, *, base: str | Path = ".env") -> list[str]:
    """Load ``.env.<env>`` first, then ``.env``; process env always wins.

    Per-world credentials (``.env.demo``, ``.env.live``) take precedence so
    one server checkout can hold two venues' keys without either process
    seeing the other's. Returns the files that contributed values.
    """
    protected = set(os.environ)
    loaded: list[str] = []
    for path in (f"{base}.{env_name}", base):
        p = Path(path)
        if not p.exists():
            continue
        touched = 0
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in protected:
                os.environ[key] = value
                protected.add(key)  # earlier files win: .env.<env> beats .env
                touched += 1
        if touched:
            loaded.append(str(p))
    return loaded


def load_env(path: str | Path = ".env") -> int:
    """Load variables from ``path``; returns the number of variables exported.

    Existing environment variables always win, and missing files are silent —
    keys may also be provided through the normal environment.
    """
    p = Path(path)
    if not p.exists():
        return 0
    loaded = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
