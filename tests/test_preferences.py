import json
import stat
from pathlib import Path

import pytest

from acornfs.errors import AcornFSError
from acornfs.preferences import (
    mount_location,
    preferences_path,
    reset_mount_location,
    set_mount_location,
)


def test_default_mount_location_remains_sidebar_friendly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACORNFS_MOUNT_ROOT", raising=False)

    location = mount_location()

    assert location.mode == "sidebar"
    assert location.source == "default"
    assert location.root.name == "AcornFS Mounts"


def test_runtime_mount_location_is_persisted_privately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ACORNFS_MOUNT_ROOT", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    (tmp_path / "runtime").mkdir()

    saved = set_mount_location("runtime")
    loaded = mount_location()

    assert saved.root == tmp_path / "runtime" / "acornfs" / "images"
    assert loaded == saved
    assert stat.S_IMODE(preferences_path().stat().st_mode) == 0o600
    assert json.loads(preferences_path().read_text(encoding="utf-8"))["mount_location"] == (
        "runtime"
    )


def test_environment_override_has_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_mount_location("sidebar")
    override = tmp_path / "environment-mounts"
    monkeypatch.setenv("ACORNFS_MOUNT_ROOT", str(override))

    location = mount_location()

    assert location.mode == "custom"
    assert location.source == "environment"
    assert location.root == override


def test_custom_location_is_canonical_and_resettable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ACORNFS_MOUNT_ROOT", raising=False)
    custom = tmp_path / "custom" / ".." / "mounts"

    assert set_mount_location(str(custom)).root == tmp_path / "mounts"
    assert mount_location().root == tmp_path / "mounts"
    assert reset_mount_location().mode == "sidebar"
    assert not preferences_path().exists()


@pytest.mark.parametrize("value", ["relative/path", "/"])
def test_unsafe_mount_locations_are_rejected(value: str) -> None:
    with pytest.raises(AcornFSError):
        set_mount_location(value)


def test_corrupt_preferences_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACORNFS_MOUNT_ROOT", raising=False)
    preferences_path().parent.mkdir(parents=True)
    preferences_path().write_text("not json", encoding="utf-8")

    with pytest.raises(AcornFSError, match="Could not read"):
        mount_location()
