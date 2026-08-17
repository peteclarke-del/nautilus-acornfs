from pathlib import Path
from unittest.mock import patch

import pytest

from acornfs.errors import AcornFSError
from acornfs.file_forge import file_forge_available, file_forge_command, open_in_file_forge
from tests.image_fixture import create_beebscsi_image


def test_configured_launcher_expands_pair_placeholders_without_a_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    monkeypatch.setenv(
        "ACORN_FILE_FORGE_COMMAND",
        "file-forge-client --descriptor {dsc} --image {dat}",
    )

    assert file_forge_command(dsc_path) == [
        "file-forge-client",
        "--descriptor",
        str(dsc_path),
        "--image",
        str(dat_path),
    ]


def test_configured_launcher_appends_both_pair_members_without_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    monkeypatch.setenv("ACORN_FILE_FORGE_COMMAND", "flatpak run example.FileForge")

    assert file_forge_command(dat_path) == [
        "flatpak",
        "run",
        "example.FileForge",
        str(dat_path),
        str(dsc_path),
    ]


def test_missing_native_launcher_is_unavailable_and_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    monkeypatch.delenv("ACORN_FILE_FORGE_COMMAND", raising=False)
    monkeypatch.setattr("acornfs.file_forge.shutil.which", lambda _name: None)
    monkeypatch.setattr("acornfs.file_forge.Path.home", lambda: tmp_path)

    assert not file_forge_available()
    with pytest.raises(AcornFSError, match="native Acorn File Forge application"):
        file_forge_command(dat_path)


def test_native_user_launcher_is_detected_when_nautilus_path_omits_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / ".local/bin/acorn-file-forge"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o700)
    monkeypatch.delenv("ACORN_FILE_FORGE_COMMAND", raising=False)
    monkeypatch.setattr("acornfs.file_forge.shutil.which", lambda _name: None)
    monkeypatch.setattr("acornfs.file_forge.Path.home", lambda: tmp_path)

    assert file_forge_available()


def test_configured_launcher_is_available_only_when_its_executable_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACORN_FILE_FORGE_COMMAND", "missing-file-forge --open {image}")
    monkeypatch.setattr("acornfs.file_forge.shutil.which", lambda _name: None)
    assert not file_forge_available()

    launcher = tmp_path / "file-forge"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o700)
    monkeypatch.setenv("ACORN_FILE_FORGE_COMMAND", f"{launcher} --open {{image}}")
    assert file_forge_available()


def test_launcher_uses_an_argv_process_and_detaches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    monkeypatch.setenv("ACORN_FILE_FORGE_COMMAND", "file-forge-client")

    with patch("acornfs.file_forge.subprocess.Popen") as popen:
        open_in_file_forge(dat_path)

    popen.assert_called_once()
    assert popen.call_args.args[0] == ["file-forge-client", str(dat_path), str(dsc_path)]
    assert popen.call_args.kwargs["start_new_session"] is True


def test_shell_metacharacters_remain_literal_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    monkeypatch.setenv(
        "ACORN_FILE_FORGE_COMMAND",
        "file-forge-client ';' '$(not-a-command)'",
    )

    assert file_forge_command(dat_path) == [
        "file-forge-client",
        ";",
        "$(not-a-command)",
        str(dat_path),
        str(dsc_path),
    ]


def test_launcher_start_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    monkeypatch.setenv("ACORN_FILE_FORGE_COMMAND", "missing-file-forge-client")

    with (
        patch("acornfs.file_forge.subprocess.Popen", side_effect=OSError("not found")),
        pytest.raises(AcornFSError, match="Could not start Acorn File Forge: not found"),
    ):
        open_in_file_forge(dat_path)
