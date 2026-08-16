"""Cooperative cancellation for long-running, safety-sensitive operations."""

from __future__ import annotations

from collections.abc import Callable

from acornfs.errors import OperationCancelled
from acornfs.i18n import _

CancellationCheck = Callable[[], bool]
ProgressCallback = Callable[[int, str], None]


def cancellation_point(cancelled: CancellationCheck | None) -> None:
    """Stop only at a boundary where the caller has left persistent state safe."""

    if cancelled is not None and cancelled():
        raise OperationCancelled(_("The operation was cancelled safely."))


def report_progress(progress: ProgressCallback | None, percent: int, message: str) -> None:
    """Publish a normalised progress update when a caller requested one."""

    if progress is not None:
        progress(max(0, min(100, percent)), message)


__all__ = ["CancellationCheck", "ProgressCallback", "cancellation_point", "report_progress"]
