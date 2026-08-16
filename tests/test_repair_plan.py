from pathlib import Path

from acornfs.core import plan_repairs
from tests.image_fixture import create_beebscsi_image, rewrite_old_map


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
    assert "Applying repairs is intentionally unsupported" in plan.format_text()
