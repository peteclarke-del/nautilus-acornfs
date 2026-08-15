# Mounting

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

For normal desktop use, install the Nautilus extension and mount from the file's
context menu as described in [nautilus.md](nautilus.md). The commands below are
the foreground terminal workflow.

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

Files and directories are presented as mode `0444` and `0555`. The default
kernel mount carries `ro,nodev,nosuid,noexec` and rejects mutations.

## Writable mount

Enable complete file and directory mutations explicitly:

```shell
acornfs mount --read-write /path/to/scsi0.dat /path/to/mountpoint
```

Read-only remains the default. A writable session takes exclusive locks on both
the DAT and DSC; read-only sessions take shared locks and refuse to start while a
writer is active. Each writable session creates a persistent checkpoint, flushes
each completed mutation, and validates ADFS before a clean unmount removes that
checkpoint. Names must obey old-ADFS rules: 7-bit ASCII, at most 10 bytes, with
no `.`, `:` or carriage return.

Before creating that checkpoint, AcornFS validates the descriptor and DAT
geometry, ADFS map and directory structures, and every used and free sector
extent. A fatal finding refuses writable access. Warnings describe unusual but
safe structures, and compatibility advice records details such as intentionally
reserved capacity; neither blocks a writable mount. A read-only mount remains
available when the directory tree can still be traversed safely.

Existing load address, execute address and access/lock metadata survive content
replacement. Acorn-locked entries are presented without POSIX write bits and
reject writes, renames and deletion. Checkpoints use filesystem reflinks when
the source and state location support them, falling back to a durable copy.

Acorn metadata is available through standard Linux extended-attribute tools:

```shell
getfattr -d /path/to/mountpoint/README
setfattr -n user.acorn.filetype -v FFD /path/to/mountpoint/README
setfattr -n user.acorn.locked -v 1 /path/to/mountpoint/README
```

`user.acorn.load` and `user.acorn.execute` use exactly eight hexadecimal digits;
`user.acorn.filetype` uses exactly three. `user.acorn.locked` accepts `0`, `1`,
`false`, or `true`. Those four attributes are writable only on a writable mount.
`user.acorn.source` (`adfs`) and `user.acorn.path` are read-only provenance
attributes. Setting a filetype changes the ADFS load/execute encoding in the
normal RISC OS fashion, so callers should not treat the raw addresses and the
filetype as independent metadata.

If a session is interrupted, inspect and resolve it explicitly:

```shell
acornfs recover /path/to/scsi0.dat
acornfs recover /path/to/scsi0.dat --restore  # undo the interrupted session
acornfs recover /path/to/scsi0.dat --discard  # accept the current image
```

Validate the ADFS structure without mounting or modifying the image:

```shell
acornfs validate /path/to/scsi0.dat
acornfs validate --json /path/to/scsi0.dat
```

Human output groups findings by `FATAL`, `WARNING`, and `ADVICE`. JSON output
contains stable finding codes, optional Acorn paths, capacity/accounting totals,
and a `safe_for_write` boolean for scripts. The command exits non-zero for fatal
findings or warnings so unattended validation can use a strict policy; advice
alone is successful. Validation never repairs or modifies the image.

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

- Manual mounts are foreground processes. Nautilus actions use a collected transient systemd
  user service when the desktop session provides one, with a detached-process fallback.
- POSIX timestamp changes are accepted for application compatibility but are not persisted.
- All entries currently use the DAT file's modification time as their POSIX time.
- Unsafe, malformed, ambiguous, or non-ADFS pairs are rejected rather than repaired.
- Filename characters unavailable on POSIX are displayed with unambiguous Unicode glyphs.
