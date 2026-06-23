from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from . import config

CACHE_VERSION = "v1"


def _cache_root() -> Path:
    return config.BATTERY_OUTPUT_ROOT / ".render_cache" / CACHE_VERSION


def _disabled() -> bool:
    return config.env_truthy("BATTERY_RENDER_CACHE_DISABLE")


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
        return None


def _atomic_write_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(data, ensure_ascii=False, default=str)
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
        tmp.write_text(blob, encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        # Cache is best-effort (spec §6); never break the request on write failure.
        pass
