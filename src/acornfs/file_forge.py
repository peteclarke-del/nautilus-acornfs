"""Shell-free launcher contract for Acorn File Forge desktop integration."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from acornfs.core import discover_pair
from acornfs.errors import AcornFSError
from acornfs.i18n import _

COMMAND_ENVIRONMENT = "ACORN_FILE_FORGE_COMMAND"
DEFAULT_EXECUTABLE = "acorn-file-forge"


def file_forge_command(image_path: str | Path) -> list[str]:
    """Build an argv-only File Forge command for a validated local image pair.

    The optional environment command may contain ``{image}``, ``{dat}``, and
    ``{dsc}`` as complete argv tokens. With no placeholders, both pair members
    are appended. No shell is involved.
    """

    selected = Path(image_path).expanduser().resolve()
    pair = discover_pair(selected)
    configured = os.environ.get(COMMAND_ENVIRONMENT, "").strip()
    if configured:
        try:
            template = shlex.split(configured)
        except ValueError as exc:
            raise AcornFSError(
                _("{variable} is not valid command syntax: {error}").format(
                    variable=COMMAND_ENVIRONMENT, error=exc
                )
            ) from exc
        if not template:
            raise AcornFSError(
                _("{variable} must name an executable.").format(variable=COMMAND_ENVIRONMENT)
            )
    else:
        executable = shutil.which(DEFAULT_EXECUTABLE)
        if executable is None:
            raise AcornFSError(
                _(
                    "Acorn File Forge has no desktop launcher installed. Install an "
                    "'{executable}' command, or set {variable} to its safe argv command. "
                    "The browser-only service cannot securely receive a local image without "
                    "a desktop hand-off helper."
                ).format(executable=DEFAULT_EXECUTABLE, variable=COMMAND_ENVIRONMENT)
            )
        template = [executable]

    values = {
        "{image}": str(selected),
        "{dat}": str(pair.dat_path),
        "{dsc}": str(pair.dsc_path),
    }
    placeholders = False
    command: list[str] = []
    for argument in template:
        if argument in values:
            placeholders = True
            command.append(values[argument])
        else:
            command.append(argument)
    if not placeholders:
        command.extend((str(pair.dat_path), str(pair.dsc_path)))
    return command


def open_in_file_forge(image_path: str | Path) -> None:
    """Launch File Forge as a detached process without invoking a shell."""

    command = file_forge_command(image_path)
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise AcornFSError(
            _("Could not start Acorn File Forge: {error}").format(error=exc)
        ) from exc


__all__ = ["COMMAND_ENVIRONMENT", "file_forge_command", "open_in_file_forge"]
