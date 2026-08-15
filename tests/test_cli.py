from pathlib import Path

from acornfs.cli import main


def test_inspect_error_is_concise(tmp_path: Path, capsys: object) -> None:
    result = main(["inspect", str(tmp_path / "missing.dat")])
    assert result == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "does not exist" in captured.err
