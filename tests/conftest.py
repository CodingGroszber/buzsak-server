from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def example_config_path() -> Path:
    return REPO_ROOT / "config" / "example.yaml"
