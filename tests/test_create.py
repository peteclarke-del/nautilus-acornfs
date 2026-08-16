import os
from pathlib import Path

import pytest

from acornfs.core import create_beebscsi_image, validate_image_report
from acornfs.errors import AcornFSError


def test_create_beebscsi_image_is_valid_and_reports_progress(tmp_path: Path) -> None:
    updates: list[tuple[int, str]] = []

    created = create_beebscsi_image(
        tmp_path,
        name="games.dat",
        title="GAMES",
        capacity="2MB",
        progress=lambda percent, message: updates.append((percent, message)),
    )

    assert created.pair.dat_path == tmp_path / "games.dat"
    assert created.pair.dsc_path == tmp_path / "games.dsc"
    assert created.capacity_bytes == created.pair.dat_path.stat().st_size
    assert created.title == "GAMES"
    assert validate_image_report(created.pair.dat_path).safe_for_write
    assert [percent for percent, _message in updates] == sorted(
        percent for percent, _message in updates
    )
    assert updates[0][0] == 0
    assert updates[-1][0] == 100


@pytest.mark.parametrize("existing", ["scsi0.dat", "SCSI0.DSC"])
def test_create_never_overwrites_either_pair_member(tmp_path: Path, existing: str) -> None:
    path = tmp_path / existing
    path.write_bytes(b"keep me")

    with pytest.raises(AcornFSError, match="overwrite"):
        create_beebscsi_image(tmp_path, capacity="2MB")

    assert path.read_bytes() == b"keep me"
    assert sorted(item.name for item in tmp_path.iterdir()) == [existing]


@pytest.mark.parametrize(
    ("keyword", "value", "message"),
    [
        ("name", "../scsi0", "filename"),
        ("title", "TITLE-TOO-LONG", "1–12"),
        ("capacity", "not-a-size", "Invalid BeebSCSI capacity"),
    ],
)
def test_create_rejects_invalid_settings(
    tmp_path: Path, keyword: str, value: str, message: str
) -> None:
    settings = {"capacity": "2MB", keyword: value}
    with pytest.raises(AcornFSError, match=message):
        create_beebscsi_image(tmp_path, **settings)
    assert list(tmp_path.iterdir()) == []


def test_create_rolls_back_if_second_file_cannot_be_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = os.link
    calls = 0

    def fail_second_link(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected descriptor publication failure")
        real_link(source, destination)

    monkeypatch.setattr("acornfs.core.create.os.link", fail_second_link)

    with pytest.raises(AcornFSError, match="Could not publish"):
        create_beebscsi_image(tmp_path, capacity="2MB")

    assert list(tmp_path.iterdir()) == []


def test_create_errors_are_translatable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("acornfs.core.create._", lambda message: f"translated: {message}")

    with pytest.raises(AcornFSError, match="translated: The image name"):
        create_beebscsi_image(tmp_path, name="../disc")
