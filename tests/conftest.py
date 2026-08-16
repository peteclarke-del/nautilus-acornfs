from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_xdg_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let recovery tests or writable sessions touch the user's state."""

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
