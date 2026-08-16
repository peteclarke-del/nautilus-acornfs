"""Shared gettext setup for desktop-facing AcornFS components."""

from __future__ import annotations

import gettext
import os
from pathlib import Path

DOMAIN = "acornfs"


def locale_directory() -> Path:
    """Return the packaged locale directory, or an explicit development override."""

    override = os.environ.get("ACORNFS_LOCALE_DIR")
    return Path(override) if override else Path(__file__).with_name("locale")


def translation() -> gettext.NullTranslations:
    """Load the active catalogue while retaining English as a safe fallback."""

    return gettext.translation(DOMAIN, localedir=locale_directory(), fallback=True)


_catalogue = translation()
_ = _catalogue.gettext
ngettext = _catalogue.ngettext


__all__ = ["DOMAIN", "_", "locale_directory", "ngettext", "translation"]
