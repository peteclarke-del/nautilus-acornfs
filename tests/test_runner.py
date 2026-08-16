from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from acornfs.errors import AcornFSError
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
    order: list[str] = []
    context.__exit__.side_effect = lambda *_args: order.append("image-exit") or False
    with (
        patch("acornfs.fuse_adapter.runner.ReadOnlyImage.open", return_value=context),
        patch("acornfs.fuse_adapter.runner.ReadOnlyOperations") as operations_class,
        patch(
            "acornfs.fuse_adapter.runner.pyfuse3.init",
            side_effect=lambda *_args: order.append("init"),
        ),
        patch(
            "acornfs.fuse_adapter.runner.pyfuse3.close",
            side_effect=lambda *_args, **_kwargs: order.append("close"),
        ),
        patch("acornfs.fuse_adapter.runner.trio.run"),
        patch(
            "acornfs.fuse_adapter.runner.register_mount",
            side_effect=lambda *_args, **_kwargs: order.append("register"),
        ) as register,
        patch(
            "acornfs.fuse_adapter.runner.unregister_mount",
            side_effect=lambda *_args: order.append("unregister"),
        ) as unregister,
    ):
        operations_class.return_value.flush_pending.side_effect = lambda: order.append("flush")
        mount_image("/images/scsi0.dat", mountpoint, read_write=True)

    register.assert_called_once_with(
        Path("/images/scsi0.dat"), mountpoint.resolve(), read_write=True
    )
    assert order == ["register", "init", "flush", "close", "image-exit", "unregister"]
    unregister.assert_called_once_with(mountpoint.resolve())


def test_failed_shutdown_flush_detaches_and_retains_unclean_context(tmp_path: Path) -> None:
    mountpoint = tmp_path / "mount"
    mountpoint.mkdir()
    image = SimpleNamespace(pair=SimpleNamespace(dat_path=Path("/images/scsi0.dat")))
    context = MagicMock()
    context.__enter__.return_value = image
    context.__exit__.return_value = False
    operations = MagicMock()
    operations.flush_pending.side_effect = AcornFSError("injected dirty-buffer failure")

    with (
        patch("acornfs.fuse_adapter.runner.ReadOnlyImage.open", return_value=context),
        patch("acornfs.fuse_adapter.runner.ReadOnlyOperations", return_value=operations),
        patch("acornfs.fuse_adapter.runner.pyfuse3.init"),
        patch("acornfs.fuse_adapter.runner.pyfuse3.close") as close,
        patch("acornfs.fuse_adapter.runner.trio.run"),
        patch("acornfs.fuse_adapter.runner.register_mount"),
        patch("acornfs.fuse_adapter.runner.unregister_mount") as unregister,
        pytest.raises(AcornFSError, match="dirty-buffer failure"),
    ):
        mount_image("/images/scsi0.dat", mountpoint, read_write=True)

    close.assert_called_once_with()
    exit_type, exit_error, _traceback = context.__exit__.call_args.args
    assert exit_type is AcornFSError
    assert isinstance(exit_error, AcornFSError)
    unregister.assert_called_once_with(mountpoint.resolve())
