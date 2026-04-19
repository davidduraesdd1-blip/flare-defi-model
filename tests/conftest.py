"""
Pytest-wide fixtures for the DeFi Model test suite.

Resets cross-app state files between tests so runs don't bleed into each
other (multisig vote cooldowns, reserved positions, etc.) and cleans up
OneDrive/Dropbox sync leftovers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_DATA = Path(__file__).resolve().parent.parent / "data"
_CROSS_APP_FILES = [
    _DATA / "pending_multisig.json",
    _DATA / "cross_app_positions.json",
    _DATA / "wallet_reservations.json",
]


def _wipe(p: Path) -> None:
    """Best-effort reset with retry — OneDrive briefly holds handles."""
    import time
    for _ in range(5):
        try:
            if p.exists():
                p.unlink()
            return
        except PermissionError:
            time.sleep(0.1)
    try:
        p.write_text("{}", encoding="utf-8")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_cross_app_state():
    """Reset cross-app state before each test so runs don't bleed."""
    for p in _CROSS_APP_FILES:
        _wipe(p)
    yield
