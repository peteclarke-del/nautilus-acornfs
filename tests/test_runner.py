from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from acornfs.fuse_adapter.runner import _contains_keyboard_interrupt, mount_image


def test_finds_keyboard_interrupt_inside_exception_group() -> None:
    error = BaseExceptionGroup("Trio nursery", [RuntimeError("other"), KeyboardInterrupt()])
    assert _contains_keyboard_interrupt(error)


def test_rejects_group_without_keyboard_interrupt() -> None:
    error = ExceptionGroup("ordinary failures", [RuntimeError("one"), ValueError("two")])
    assert not _contains_keyboard_interrupt(error)


def test_mount_registration_spans_fuse_and_image_shutdown(tmp_path: Path) -> None:
    mountpoint = tmp_path / "mount"
    mountpoint.mkdir()
    image = SimpleNamespace(pair=SimpleNamespace(dat_path=Path("/images/scsi0.dat")))
    context = MagicMock()
    context.__enter__.return_value = image
    context.__exit__.return_value = False
    order: list[str] = []
    with (
        patch("acornfs.fuse_adapter.runner.ReadOnlyImage.open", return_value=context),
        patch("acornfs.fuse_adapter.runner.ReadOnlyOperations"),
        patch(
            "acornfs.fuse_adapter.runner.pyfuse3.init",
            side_effect=lambda *_args: order.append("init"),
        ),
        patch("acornfs.fuse_adapter.runner.pyfuse3.close"),
        patch("acornfs.fuse_adapter.runner.trio.run"),
        patch(
            "acornfs.fuse_adapter.runner.register_mount",
            side_effect=lambda *_args, **_kwargs: order.append("register"),
        ) as register,
        patch("acornfs.fuse_adapter.runner.unregister_mount") as unregister,
    ):
        mount_image("/images/scsi0.dat", mountpoint, read_write=True)

    register.assert_called_once_with(
        Path("/images/scsi0.dat"), mountpoint.resolve(), read_write=True
    )
    assert order == ["register", "init"]
    unregister.assert_called_once_with(mountpoint.resolve())
