"""Point the bundled-CLI lookup at an empty folder by default, so tests that stub an assembly-app
checkout (ASSEMBLY_APP_DIR) exercise that stub, not the real cli/bin bundles."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_bundled_cli(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSEMBLY_CLI_DIR", str(tmp_path_factory.mktemp("no-cli-bundles")))
