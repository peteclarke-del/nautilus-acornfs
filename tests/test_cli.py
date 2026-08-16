from pathlib import Path
from unittest.mock import patch

from acornfs.cli import main
from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.mounts import MountRecord
from tests.image_fixture import create_beebscsi_image, set_root_entry_length


def test_inspect_error_is_concise(tmp_path: Path, capsys: object) -> None:
    result = main(["inspect", str(tmp_path / "missing.dat")])
    assert result == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "does not exist" in captured.err


def test_create_beebscsi_command_creates_pair(tmp_path: Path, capsys: object) -> None:
    assert (
        main(
            [
                "create-beebscsi",
                str(tmp_path),
                "--name",
                "new-disc",
                "--title",
                "NEWDISC",
                "--capacity",
                "2MB",
            ]
        )
        == 0
    )
    assert (tmp_path / "new-disc.dat").is_file()
    assert (tmp_path / "new-disc.dsc").is_file()
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Created and verified" in output
    assert "ADFS title: NEWDISC" in output


def test_metadata_aware_export_and_import_commands(tmp_path: Path, capsys: object) -> None:
    images = tmp_path / "images"
    images.mkdir()
    dat_path, _dsc_path = create_beebscsi_image(images)
    exported = tmp_path / "GUIDE"

    assert main(["export-file", str(dat_path), "$.DOCS.GUIDE", str(exported)]) == 0
    assert exported.read_bytes() == b"Nested file\r"
    assert exported.with_name("GUIDE.inf").is_file()
    assert "Acorn metadata:" in capsys.readouterr().out  # type: ignore[attr-defined]

    assert main(["import-file", str(dat_path), str(exported), "--name", "COPY"]) == 0
    assert "Metadata source: INF sidecar GUIDE.inf" in capsys.readouterr().out  # type: ignore[attr-defined]
    with ReadOnlyImage.open(dat_path) as image:
        copied = image.lookup(ROOT_INODE, b"COPY")
        assert copied is not None
        assert image.read(copied.inode, 0, copied.size) == b"Nested file\r"


def test_status_reports_no_mounts(capsys: object) -> None:
    with patch("acornfs.cli.active_mounts", return_value=[]):
        assert main(["status"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.out == "No AcornFS mounts found.\n"


def test_diagnostics_json_is_exportable(capsys: object) -> None:
    report = {
        "privacy": "No image contents or absolute paths are included.",
        "runtime": {},
        "fuse": {},
        "mounts": [],
    }
    with patch("acornfs.diagnostics.diagnostic_report", return_value=report):
        assert main(["diagnostics", "--json"]) == 0
    assert '"privacy"' in capsys.readouterr().out  # type: ignore[attr-defined]


def test_config_mount_location_round_trip(
    tmp_path: Path, capsys: object, monkeypatch: object
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))  # type: ignore[attr-defined]
    monkeypatch.delenv("ACORNFS_MOUNT_ROOT", raising=False)  # type: ignore[attr-defined]

    assert main(["config-mount-location", "runtime"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert str(runtime / "acornfs" / "images") in output
    assert "Mode: runtime; source: user" in output
    assert main(["config-mount-location"]) == 0
    assert "Mode: runtime; source: user" in capsys.readouterr().out  # type: ignore[attr-defined]
    assert main(["config-mount-location", "--reset"]) == 0
    assert "Mode: sidebar; source: default" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_lazy_unmount_is_refused_for_writable_image(tmp_path: Path, capsys: object) -> None:
    mountpoint = tmp_path / "mounted"
    mountpoint.mkdir()
    record = MountRecord(str(mountpoint), "scsi0.dat", "rw", read_write=True)
    with (
        patch("acornfs.cli.mount_at", return_value=record),
        patch("acornfs.cli.subprocess.run") as run,
    ):
        assert main(["unmount", "--lazy", str(mountpoint)]) == 2
    run.assert_not_called()
    assert "read-only" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_writable_cli_unmount_confirms_finalisation(tmp_path: Path) -> None:
    mountpoint = tmp_path / "mounted"
    mountpoint.mkdir()
    record = MountRecord(
        str(mountpoint),
        "scsi0.dat",
        "rw",
        image_path=str(tmp_path / "scsi0.dat"),
        read_write=True,
    )
    result = type("Result", (), {"returncode": 0, "stderr": ""})()
    with (
        patch("acornfs.cli.mount_at", return_value=record),
        patch("acornfs.cli.subprocess.run", return_value=result),
        patch("acornfs.cli.wait_for_mount_shutdown", return_value=True) as wait,
        patch("acornfs.cli.pending_recovery", return_value=None),
    ):
        assert main(["unmount", str(mountpoint)]) == 0
    wait.assert_called_once_with(mountpoint.resolve())


def test_recover_command_reports_and_restores_checkpoint(tmp_path: Path, capsys: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    image = ReadOnlyImage.open(dat_path, writable=True)
    readme = image.lookup(ROOT_INODE, b"README")
    assert readme is not None
    image.replace_file(readme.inode, b"temporary")
    image.close(clean=False)

    assert main(["recover", str(dat_path)]) == 0
    assert "Use --restore" in capsys.readouterr().out  # type: ignore[attr-defined]
    assert main(["recover", "--restore", str(dat_path)]) == 0
    assert "restored" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_desktop_mount_forwards_writable_choice() -> None:
    with patch("acornfs.desktop.desktop_mount", return_value=0) as desktop_mount:
        assert main(["desktop-mount", "--read-write", "/image.dat"]) == 0
    desktop_mount.assert_called_once_with("/image.dat", read_write=True)


def test_desktop_open_forwards_uri_list() -> None:
    with patch("acornfs.desktop.desktop_open", return_value=0) as desktop_open:
        assert main(["desktop-open", "file:///image.dat", "acornfs:///image.dsc"]) == 0
    desktop_open.assert_called_once_with(["file:///image.dat", "acornfs:///image.dsc"])


def test_desktop_create_forwards_directory() -> None:
    with patch("acornfs.desktop.desktop_create", return_value=0) as desktop_create:
        assert main(["desktop-create", "/images"]) == 0
    desktop_create.assert_called_once_with("/images")


def test_desktop_mount_location_forwards_to_desktop() -> None:
    with patch("acornfs.desktop.desktop_configure_mount_location", return_value=0) as configure:
        assert main(["desktop-configure-mount-location"]) == 0
    configure.assert_called_once_with()


def test_validate_command_reports_clean_image(tmp_path: Path, capsys: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    assert main(["validate", str(dat_path)]) == 0
    assert "passed with no problems" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_validate_json_reports_write_gate_and_extent_counts(tmp_path: Path, capsys: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    assert main(["validate", "--json", str(dat_path)]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"safe_for_write": true' in output
    assert '"used_sectors": 19' in output
    assert '"free_sectors": 5261' in output


def test_validate_command_reports_corrupt_subdirectory(tmp_path: Path, capsys: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path) as image:
        docs = image.lookup(ROOT_INODE, b"DOCS")
        assert docs is not None
        _parent, entry = image._mount._navigate(docs.acorn_path)._resolve()  # type: ignore[attr-defined]
        offset = entry.start_sector * 256 + (5 * 256 - 1)
    with dat_path.open("r+b") as handle:
        handle.seek(offset)
        original = handle.read(1)
        handle.seek(offset)
        handle.write(bytes([original[0] ^ 0xFF]))

    assert main(["validate", str(dat_path)]) == 1
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Validation found" in output
    assert "DOCS" in output


def test_validate_command_rejects_truncated_dat_cleanly(tmp_path: Path, capsys: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with dat_path.open("r+b") as handle:
        handle.truncate(512)
    assert main(["validate", str(dat_path)]) == 1
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "geometry.dat_short" in output
    assert "adfs.open_failed" in output


def test_repair_plan_json_is_dry_run_and_does_not_modify_image(
    tmp_path: Path, capsys: object
) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    dsc_path.write_bytes(b"broken")
    before = dat_path.read_bytes()
    assert main(["repair-plan", "--json", str(dat_path)]) == 1
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"mode": "dry-run"' in output
    assert '"application_supported": false' in output
    assert dat_path.read_bytes() == before


def test_repair_command_applies_eligible_plan_and_reports_audit(
    tmp_path: Path, capsys: object, monkeypatch: object
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    set_root_entry_length(dat_path, "DOCS", 0)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # type: ignore[attr-defined]

    assert main(["repair", str(dat_path), "--confirm", dat_path.name]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Applied 1 repair action" in output
    assert "passed with no problems" in output
    assert "Audit report:" in output
