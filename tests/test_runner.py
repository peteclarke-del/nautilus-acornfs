from acornfs.fuse_adapter.runner import _contains_keyboard_interrupt


def test_finds_keyboard_interrupt_inside_exception_group() -> None:
    error = BaseExceptionGroup("Trio nursery", [RuntimeError("other"), KeyboardInterrupt()])
    assert _contains_keyboard_interrupt(error)


def test_rejects_group_without_keyboard_interrupt() -> None:
    error = ExceptionGroup("ordinary failures", [RuntimeError("one"), ValueError("two")])
    assert not _contains_keyboard_interrupt(error)
