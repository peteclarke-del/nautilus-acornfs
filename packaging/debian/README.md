# Debian package

AcornFS produces one installable package for Ubuntu 24.04 LTS on amd64:

```text
nautilus-acornfs_VERSION_amd64.deb
```

The package contains the command, FUSE adapter, Nautilus extension, MIME and
desktop integration, documentation, and the exact Oaknut 12.15.1 runtime. A
single package keeps the command and desktop extension on the same version and
matches the supported desktop product boundary. Headless and other
architecture packages can be split later if there is a tested use case.

## Runtime dependency policy

Ubuntu supplies Python, FUSE, pyfuse3, Trio, Stevedore, Nautilus and the desktop
utilities through normal package dependencies. The Oaknut runtime,
`exit-codes` and `typename` are not available in Ubuntu 24.04. Their audited,
MIT-licensed, pure-Python wheels are listed with exact versions and SHA-256
hashes in `vendor-requirements.txt` and installed below
`/usr/lib/python3/dist-packages`.

The build refuses an unexpected version, hash, licence, platform wheel,
archive path or duplicate installed path. Package installation never invokes
`pip`, accesses the network, compiles code or writes to a user's home
directory. Maintainer scripts only refresh the shared MIME and desktop
databases.

The package declares these Ubuntu runtime dependencies:

```text
python3 (>= 3.11~)
python3 (<< 3.13)
fuse3
python3-pyfuse3 (>= 3.3)
python3-trio (>= 0.24)
python3-stevedore (>= 1:5.0)
python3-nautilus
gir1.2-nautilus-4.0
shared-mime-info
desktop-file-utils
libnotify-bin
zenity
```

The package installs `/usr/bin/acornfs` and uses `/usr/bin/python3`. It does
not modify `fuse.conf`, create setuid helpers or enumerate home directories.
Preferences, checkpoints, audit records, images and mount directories remain
user-owned and survive upgrades and package removal.

Greaseweazle is an optional external integration and is not bundled into the
Debian package. Install its current host tools separately when HFE v1/HFEv3
mounting or physical-floppy writing is required, and ensure `gw` is available
in the graphical session's `PATH`. Raw ADFS, DFS, MMB, FileCore, BeebSCSI and
ROMFS operation does not require Greaseweazle.

## Reproducible build

Create the package from an amd64 checkout with the release dependencies
installed:

```shell
python3 -m venv .venv
. .venv/bin/activate
python -m pip install '.[release]'
make deb
```

`make deb` derives `SOURCE_DATE_EPOCH` from the current commit unless it is set
explicitly. It builds the package twice, compares the SHA-256 digests and
writes the verified package and `nautilus-acornfs-deb-manifest.json` below
`build/debian`. An exact `vMAJOR.MINOR.PATCH` tag produces that release version;
other commits receive a `+gitYYYYMMDD.REVISION` Debian version suffix.

The manifest records every installed file, system dependency, vendored wheel
version, licence and hash. CI installs the artifact on Ubuntu 24.04, checks the
command, Python imports and desktop loader, removes the package, and confirms
the command was removed. The release builder includes the same `.deb` and
manifest in `SHA256SUMS`.
