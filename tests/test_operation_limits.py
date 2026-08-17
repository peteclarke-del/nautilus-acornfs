from pathlib import Path

import pytest

from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.core.properties import read_image_properties
from acornfs.core.repair import apply_repairs, plan_repairs
from acornfs.core.validation import validate_image_report
from acornfs.errors import OperationLimitExceeded
from acornfs.operations import OperationBudget
from tests.image_fixture import create_adfs_floppy, create_beebscsi_image, set_root_entry_length


def _expired_budget() -> OperationBudget:
    return OperationBudget(deadline=0.0, clock=lambda: 1.0)


@pytest.mark.parametrize("operation", ["validation", "properties", "repair-plan"])
def test_inspection_operations_stop_at_their_wall_clock_budget(
    tmp_path: Path, operation: str
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)

    with pytest.raises(OperationLimitExceeded, match="safe time limit"):
        if operation == "validation":
            validate_image_report(dat_path, budget=_expired_budget())
        elif operation == "properties":
            read_image_properties(dat_path, budget=_expired_budget())
        else:
            plan_repairs(dat_path, budget=_expired_budget())


def test_validation_stops_at_total_item_limit(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    budget = OperationBudget.create(timeout=30, max_items=1)

    with pytest.raises(OperationLimitExceeded, match="safe item limit"):
        validate_image_report(dat_path, budget=budget)


def test_validation_stops_at_directory_depth_limit(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        docs = image.lookup(ROOT_INODE, b"DOCS")
        assert docs is not None
        nested = image.make_directory(docs.inode, b"NESTED")
        image.make_directory(nested.inode, b"DEEP")

    budget = OperationBudget.create(timeout=30, max_depth=1)
    with pytest.raises(OperationLimitExceeded, match="directory-depth limit"):
        validate_image_report(dat_path, budget=budget)


def test_standalone_properties_preserve_operation_limit_error(tmp_path: Path) -> None:
    image_path = create_adfs_floppy(tmp_path)
    times = iter((0.0, 0.0, 0.0, 31.0))
    budget = OperationBudget.create(timeout=30, clock=lambda: next(times))

    with pytest.raises(OperationLimitExceeded, match="safe time limit"):
        read_image_properties(image_path, budget=budget)


def test_repair_timeout_precedes_audit_checkpoint_and_mutation(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    set_root_entry_length(dat_path, "DOCS", 0)
    before = dat_path.read_bytes()

    with pytest.raises(OperationLimitExceeded, match="safe time limit"):
        apply_repairs(dat_path, confirmation=dat_path.name, budget=_expired_budget())

    assert dat_path.read_bytes() == before
    assert not (tmp_path / "state").exists()
