import json
from pathlib import Path

import pytest

from acornfs.core import apply_repairs, plan_repairs
from acornfs.core.image import ReadOnlyImage
from acornfs.errors import AcornFSError
from acornfs.recovery import pending_recovery, recover_image
from tests.image_fixture import (
    create_beebscsi_image,
    rewrite_old_map,
    set_root_entry_length,
    set_root_entry_start,
)


def test_clean_image_has_empty_non_applicable_plan(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    before = dat_path.read_bytes()
    plan = plan_repairs(dat_path)

    assert plan.clean
    assert plan.actions == ()
    assert not plan.application_supported
    assert "not modified" not in plan.format_text()
    assert dat_path.read_bytes() == before


def test_free_space_damage_produces_one_grouped_rebuild_candidate(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)

    def damage(data: bytearray) -> None:
        data[0:3] = (7).to_bytes(3, "little")

    rewrite_old_map(dat_path, damage)
    before = dat_path.read_bytes()
    plan = plan_repairs(dat_path)

    action = next(item for item in plan.actions if item.action == "rebuild_free_space_map")
    assert action.automatic_candidate
    assert "extent.free_used_overlap" in action.finding_codes
    assert plan.as_dict()["mode"] == "dry-run"
    assert plan.as_dict()["application_supported"] is False
    assert dat_path.read_bytes() == before


def test_invalid_descriptor_requires_manual_geometry_decision(tmp_path: Path) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    dsc_path.write_bytes(b"broken")
    plan = plan_repairs(dat_path)

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.action == "restore_geometry"
    assert action.requires_manual_decision
    assert not action.automatic_candidate
    assert "cannot be applied automatically" in plan.format_text()


def test_low_risk_plan_requires_exact_confirmation(tmp_path: Path, monkeypatch: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    set_root_entry_length(dat_path, "DOCS", 0)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # type: ignore[attr-defined]

    plan = plan_repairs(dat_path)
    assert plan.application_supported
    assert "acornfs repair IMAGE" in plan.format_text()
    with pytest.raises(AcornFSError, match="exactly match"):
        apply_repairs(dat_path, confirmation="yes")
    assert pending_recovery(dat_path) is None
    assert not (tmp_path / "state").exists()


def test_low_risk_repair_is_checkpointed_verified_and_audited(
    tmp_path: Path, monkeypatch: object
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    set_root_entry_length(dat_path, "DOCS", 0)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # type: ignore[attr-defined]

    result = apply_repairs(dat_path, confirmation=dat_path.name)

    assert result.report.findings == ()
    assert plan_repairs(dat_path).clean
    assert pending_recovery(dat_path) is None
    audit = json.loads(Path(result.audit_path).read_text(encoding="utf-8"))
    assert audit["status"] == "completed"
    assert audit["checkpoint_created"] is True
    assert audit["checkpoint_retained"] is False
    assert audit["applied_actions"][0]["action"] == "normalise_directory_lengths"
    assert audit["post_validation"]["safe_for_write"] is True


def test_empty_file_extent_repair_clears_only_stale_catalogue_field(
    tmp_path: Path, monkeypatch: object
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        image.create_file(1, b"ZERO")
    set_root_entry_start(dat_path, "ZERO", 2)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # type: ignore[attr-defined]

    plan = plan_repairs(dat_path)
    assert [action.action for action in plan.actions] == ["clear_empty_file_extents"]
    result = apply_repairs(dat_path, confirmation=dat_path.name)

    assert result.report.findings == ()
    with ReadOnlyImage.open(dat_path) as image:
        _parent, entry = image._mount._adfs.path("$.ZERO")._resolve()  # type: ignore[attr-defined]
        assert entry.length == 0
        assert entry.start_sector == 0


def test_failed_repair_retains_checkpoint_and_failed_audit(
    tmp_path: Path, monkeypatch: object
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    set_root_entry_length(dat_path, "DOCS", 0)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # type: ignore[attr-defined]

    def fail(*_args: object, **_kwargs: object) -> None:
        assert pending_recovery(dat_path) is not None
        raise RuntimeError("injected repair failure")

    monkeypatch.setattr("acornfs.core.image.ReadOnlyImage.apply_catalogue_repair", fail)  # type: ignore[attr-defined]
    with pytest.raises(AcornFSError, match="checkpoint was retained"):
        apply_repairs(dat_path, confirmation=dat_path.name)

    assert pending_recovery(dat_path) is not None
    audits = list((tmp_path / "state" / "acornfs" / "repair-audits").glob("*.json"))
    audit = json.loads(audits[0].read_text(encoding="utf-8"))
    assert audit["status"] == "failed"
    assert audit["checkpoint_retained"] is True
    recover_image(dat_path, discard=True)
