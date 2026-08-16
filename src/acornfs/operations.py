"""Cooperative cancellation for long-running, safety-sensitive operations."""

from __future__ import annotations

from collections.abc import Callable

from acornfs.errors import OperationCancelled

CancellationCheck = Callable[[], bool]


def cancellation_point(cancelled: CancellationCheck | None) -> None:
    """Stop only at a boundary where the caller has left persistent state safe."""

    if cancelled is not None and cancelled():
        raise OperationCancelled("The operation was cancelled safely.")


__all__ = ["CancellationCheck", "cancellation_point"]
