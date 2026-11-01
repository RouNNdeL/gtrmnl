"""Small persistent state in /data/state.json, written atomically."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class State:
    last_hash: str | None = None
    last_push: float | None = None  # epoch of last accepted push
    last_success: float | None = None  # epoch of last complete run
    learned_limit: int | None = None  # payload limit learned from a 422
    backoff_until: float | None = None  # after a 429
    auth_failed: bool = False
    auth_flag_pushed: bool = False
    last_error: str | None = None
    last_size: int | None = None

    @classmethod
    def load(cls, path: Path) -> State:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in names})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=1)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def effective_limit(self, configured: int) -> int:
        return min(configured, self.learned_limit) if self.learned_limit else configured
