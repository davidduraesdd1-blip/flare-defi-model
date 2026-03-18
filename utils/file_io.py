"""
Shared file I/O utilities.
Centralises the atomic JSON write pattern used across the codebase.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def atomic_json_write(path: Path, data: Any) -> bool:
    """
    Write data as JSON atomically: writes to a temp file then renames to path.
    Prevents file corruption if the process crashes during a write.
    Returns True on success, False on failure (error is logged).
    """
    tmp_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        logger.error(f"Could not write {path.name}: {e}")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return False
