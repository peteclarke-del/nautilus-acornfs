# Debian packaging contract

The first release target is Ubuntu 24.04 LTS on amd64. AcornFS is not yet
published as a Debian package, but the intended package boundary and runtime
dependencies are fixed here so a later `debian/` directory does not have to
guess at them.

## Intended binary packages

| Package | Contents | Dependencies |
| --- | --- | --- |
| `nautilus-acornfs-core` | Python filesystem core, `acornfs` command, translations and documentation | Python 3.11 or later and the exactly pinned Oaknut 12.15.1 family from `pyproject.toml` |
| `nautilus-acornfs-fuse` | FUSE mounting support | core package, `fuse3`, `python3-pyfuse3` and `python3-trio` |
| `nautilus-acornfs-nautilus` | Nautilus extension, MIME and desktop integration | core and FUSE packages, `python3-nautilus`, `gir1.2-nautilus-4.0`, `shared-mime-info`, `desktop-file-utils`, `libnotify-bin` and `zenity` |

The core remains independently usable for inspection, validation, repair and
metadata-aware transfer. FUSE and Nautilus are separate dependencies because
headless systems should not acquire a desktop stack. The Nautilus package may
invoke `acornfs install-nautilus` for individual users, but package maintainer
scripts must never enumerate home directories, remove user preferences, or
delete recovery state.

## Ubuntu 24.04 dependency mapping

The supported host package names are:

```text
fuse3
python3-pyfuse3
python3-trio
python3-nautilus
gir1.2-nautilus-4.0
shared-mime-info
desktop-file-utils
libnotify-bin
zenity
```

`libfuse3-dev` and `pkg-config` are build dependencies when pyfuse3 is built
from PyPI; they are not runtime dependencies. `python3-venv` is needed only for
the documented source/virtual-environment installation. The Oaknut 12.15.1
family is not an Ubuntu 24.04 archive package, so a policy-compliant source
package needs an independently maintained Oaknut Debian package or a reviewed
vendoring plan.

## Blockers before producing `.deb` files

1. Add the project distribution licence and its verbatim Debian copyright
   metadata. Debian Policy requires every binary package to ship it as
   `/usr/share/doc/PACKAGE/copyright`.
2. Package the pinned Oaknut dependency independently, or approve and audit a
   reproducible vendoring design including every transitive licence.

Do not work around either blocker with a placeholder licence, an unpinned
network download during package assembly, or a maintainer script that invokes
`pip` as root. Once resolved, build all three packages from one source package,
declare `Rules-Requires-Root: no`, and run the lifecycle smoke test in
`tools/package_smoke.py` against the installed artefacts.
