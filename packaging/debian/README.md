# Debian packaging contract

The first release target is Ubuntu 24.04 LTS on amd64. AcornFS is not yet
published as a Debian package. `make debian-staging` verifies the package
boundary and runtime dependencies. It builds one project wheel, partitions it
into the three roots below, generates
system desktop integration from the same source as the per-user installer, and
writes `build/debian-staging/manifest.json`.

The staging output is not a `.deb` and is not distributable. Its
manifest records `publishable: false` and the unresolved blocker. CI retains
the manifest so file ownership and dependencies cannot drift unnoticed while
the legal and dependency decisions remain open.

## Intended binary packages

| Package | Contents | Dependencies |
| --- | --- | --- |
| `nautilus-acornfs-core` | Python filesystem core, `acornfs` command, distribution metadata and documentation | Python 3.11 or later and the Oaknut 12.15.1 dependency family defined in `pyproject.toml` |
| `nautilus-acornfs-fuse` | `acornfs.fuse_adapter` | same-version core package, `fuse3`, `python3-pyfuse3` and `python3-trio` |
| `nautilus-acornfs-nautilus` | `acornfs_nautilus`, system Nautilus loader, MIME and desktop integration | same-version core and FUSE packages, `python3-nautilus`, `gir1.2-nautilus-4.0`, `shared-mime-info`, `desktop-file-utils`, `libnotify-bin` and `zenity` |

The core remains independently usable for inspection, validation, repair and
metadata-aware transfer. FUSE and Nautilus are separate dependencies because
headless systems should not acquire a desktop stack. The Nautilus package may
invoke `acornfs install-nautilus` for individual users, but package maintainer
scripts must never enumerate home directories, remove user preferences, or
delete recovery state.

Staging refuses a symbolic-link or non-empty output directory, rejects unsafe
or non-regular wheel members, and verifies that no installed path belongs to
more than one binary package. The generated command uses `/usr/bin/python3` and
the system desktop loader invokes `/usr/bin/acornfs` without a shell.

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

1. Package the pinned Oaknut dependency independently, or approve and audit a
   reproducible vendoring design including every transitive licence.

Do not work around this blocker with an unpinned network download during
package assembly or a maintainer script that invokes
`pip` as root. Staging does not weaken these gates. Once resolved, turn the
tested manifest into one Debian source package, declare
`Rules-Requires-Root: no`, build all three binary packages, and run the lifecycle
smoke test in `tools/package_smoke.py` against the installed artefacts.
