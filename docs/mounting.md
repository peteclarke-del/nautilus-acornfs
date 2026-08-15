# Read-only mounting

## Requirements

The current implementation supports Linux on amd64 with Python 3.11 or later,
FUSE 3, and a valid paired BeebSCSI DAT/DSC image containing old-format ADFS.
On Ubuntu 24.04 or later, install the host packages with:

```shell
sudo apt install python3-venv fuse3 libfuse3-dev pkg-config
```

Then install AcornFS from the repository checkout:

```shell
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[fuse]'
```

## Mount and browse

```shell
mkdir -p "$HOME/AcornFS/scsi0"
acornfs inspect /path/to/scsi0.dsc
acornfs mount /path/to/scsi0.dsc "$HOME/AcornFS/scsi0"
```

Keep that terminal open. The same folder can be traversed from another terminal
or opened in Nautilus:

```shell
find "$HOME/AcornFS/scsi0" -maxdepth 3
nautilus "$HOME/AcornFS/scsi0"
```

Files and directories are presented as mode `0444` and `0555`. The kernel mount
also carries `ro,nodev,nosuid,noexec`; AcornFS implements no mutating FUSE
operations.

## Status and unmounting

```shell
acornfs status
acornfs unmount "$HOME/AcornFS/scsi0"
```

Unmounting causes the foreground mount command to exit. `Ctrl-C` in the mount
terminal also unmounts cleanly. If Nautilus keeps the location busy, close its
window and retry, or detach this read-only mount explicitly:

```shell
acornfs unmount --lazy "$HOME/AcornFS/scsi0"
```

## Current limits

- The mount process is foreground-only; a user service comes later.
- Acorn extended attributes are not exposed yet.
- All entries currently use the DAT file's modification time as their POSIX time.
- Unsafe, malformed, ambiguous, or non-ADFS pairs are rejected rather than repaired.
- Filename characters unavailable on POSIX are displayed with unambiguous Unicode glyphs.
