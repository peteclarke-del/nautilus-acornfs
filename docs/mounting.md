# Mounting

## Requirements

The current implementation supports Linux on amd64 with Python 3.11 or later,
FUSE 3, and either a valid paired BeebSCSI DAT/DSC image, a standalone ADFS
S/M/L/D/E/E+/F/F+/G/G+ floppy, an Acorn/Watford DFS SSD/DSD image, a standard
or extended MMB container, a standalone FileCore/unpaired raw ADFS hard disc, or
an Acorn ROMFS paged-ROM image. All disk formats may be mounted read-write;
ROMFS remains read-only.
For ordinary desktop use, install the `.deb` as described in the root README's
[installation guide](../README.md#installation). It resolves the host packages
through `apt`.

For source development on Ubuntu 24.04, install the build dependencies and
AcornFS from a repository checkout:

```shell
sudo apt install python3-venv python3-dev build-essential fuse3 libfuse3-dev pkg-config
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[fuse]'
```

To exercise the real kernel FUSE lifecycle rather than the in-process adapter
tests, use `make test-live`. The suite is opt-in because an exposed
`/dev/fuse` device alone does not prove that a container or CI runner has mount
permission. The dedicated amd64 live-FUSE CI job therefore verifies that the
runner can open the device before executing the suite; it fails instead of
silently accepting skipped live tests when that capability is unavailable.

## Mount and browse

Create a new empty old-format ADFS BeebSCSI pair when required:

```shell
acornfs create-beebscsi /path/to/images --name scsi0 --title BLANK --capacity 20MB
```

The command accepts a destination directory, never overwrites an existing DAT
or DSC member, validates the full temporary filesystem, and publishes the
pair together. Capacity uses Oaknut size syntax such as `2MB`, `20MB` or `512MB`
subject to BeebSCSI DSC and old-map ADFS limits.

For desktop use, install the Nautilus extension and mount from the file's
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

Standalone ADFS floppies use the same command. S/M/L commonly use `.ads`,
`.adm` and `.adl`; D through G+ commonly use the ambiguous `.adf`. Arbitrary
filenames also work when content identifies the map, directory format and exact
geometry:

```shell
acornfs mount /path/to/disc.adl "$HOME/AcornFS/floppy"
```

Add `--read-write` to edit the floppy. Old, New and Big directory formats retain
their native filename and directory limits; Big-directory long names are
preserved and may be created or changed.

Content-valid FileCore `.hdf`/`.hd4` images and raw ADFS hard discs without a
DSC use the same command. AcornFS reports their logical map geometry
but does not fabricate the physical CHS that only a descriptor can establish:

```shell
acornfs mount /path/to/riscos.hdf "$HOME/AcornFS/filecore"
```

They may be opened read-write after their New Map or old-map structure passes
validation.

The privileged amd64 integration suite mounts both DAT-selected and
DSC-selected pairs through real kernel FUSE, traverses them with `find`, `cat`
and `stat`, and verifies that writable create/edit/rename/move/delete operations
plus Acorn load, execute, filetype and lock metadata survive validation and a
fresh read-only reopen.

DFS images use the same command:

```shell
acornfs mount /path/to/disc.ssd "$HOME/AcornFS/dfs"
```

DFS has catalogue-letter prefixes, not nested on-disc directories. AcornFS
presents each populated prefix (`$`, `A`-`Z`) as a directory. A double-sided
DSD adds top-level `0` and `2` directories, matching the BBC drive designations;
each contains that side's independent prefix directories. This mapping is
virtual and does not add directory records to the image. File and metadata
mutations stay within one side; cross-side renames are refused.

Standard and extended MMB containers also use the same command:

```shell
acornfs mount /path/to/BEEB.MMB "$HOME/AcornFS/mmb"
```

Only formatted slots appear. Each is named with its zero-padded global slot
number and catalogue label, and contains the normal DFS prefix-directory view.
All declared extended-MMB catalogues are validated before traversal. See
[mmb.md](mmb.md) for writable-slot behaviour and container limits.
On a read-write mount, file and metadata changes are accepted only inside slots
whose MMB catalogue status is read-write. Locked slots remain readable and
reject mutation. Renames between slots are refused.

CRC-valid 8 KiB and 16 KiB ROMFS images also use the same command; recognition
does not depend on a `.rom` suffix:

```shell
acornfs mount /path/to/utilities.rom "$HOME/AcornFS/romfs"
```

The flat catalogue appears at the mount root. Names are case-sensitive and
on-disc `/` characters are displayed as `∕`. ROMFS remains read-only.

## Writable mount

Enable file and directory writes for any supported disk image:

```shell
acornfs mount --read-write /path/to/image /path/to/mountpoint
```

Read-only remains the default. A writable session takes an exclusive lock on
each image member; read-only sessions take shared locks and refuse to start
while a writer is active. Each writable session creates a persistent checkpoint,
flushes each completed mutation and validates the filesystem before a clean
unmount removes the checkpoint. ADFS names use 7-bit ASCII and exclude `.`, `:`
and carriage return. Old and New directories allow 10 bytes, Big directories
allow 255 bytes, and DFS names allow 7 bytes.

While mounted, a private runtime record ties the kernel mount to the canonical
image paths, device/inode identities, daemon PID and access mode. The
kernel mount table remains authoritative; dead records are discarded. Replacing
a pair at the same pathname cannot silently alias the already-mounted image.

Before creating that checkpoint, AcornFS runs the format's structural validator.
BeebSCSI old-map validation additionally checks descriptor geometry and complete
used/free extent accounting. A fatal finding refuses writable access. Warnings
and compatibility advice do not block a mount. Read-only traversal remains
available where the format can still be opened safely.

Existing load address, execute address and access/lock metadata survive content
replacement. Acorn-locked entries are presented without POSIX write bits and
reject writes, renames and deletion. Checkpoints use filesystem reflinks when
the source and state location support them, falling back to a durable copy.

Every file, directory, rename and metadata mutation has a private before-image
in addition to the session checkpoint. Old-map ADFS captures only affected
sectors. New Map ADFS, DFS and MMB take a reflink in the private recovery
directory when possible and fall back to a bounded full-image copy. If an
operation fails after changing the image, AcornFS restores and validates that
before-image before returning the error. The mount remains writable only after
rollback is verified; otherwise it fails closed and retains the persistent
checkpoint. Oversized writes and truncates are rejected before their FUSE
buffers grow.

Acorn metadata is available through standard Linux extended-attribute tools:

```shell
getfattr -d /path/to/mountpoint/README
setfattr -n user.acorn.filetype -v FFD /path/to/mountpoint/README
setfattr -n user.acorn.locked -v 1 /path/to/mountpoint/README
```

`user.acorn.load` and `user.acorn.execute` use exactly eight hexadecimal digits;
`user.acorn.filetype` uses exactly three. `user.acorn.locked` accepts `0`, `1`,
`false`, or `true`. Those four attributes are writable only on a writable mount.
`user.acorn.source` and `user.acorn.path` are read-only provenance
attributes. Setting a filetype changes the ADFS load/execute encoding in the
normal RISC OS fashion, so callers should not treat the raw addresses and the
filetype as independent metadata.

If a session is interrupted, inspect and resolve it:

```shell
acornfs recover /path/to/scsi0.dat
acornfs recover /path/to/scsi0.dat --restore  # undo the interrupted session
acornfs recover /path/to/scsi0.dat --discard  # accept the current image
```

The same commands accept a standalone ADFS, DFS or MMB image. For a New Map
DAT/DSC pair, either member identifies the checkpoint; recovery restores the DAT
and preserves the descriptor because writable filesystem operations do not
change it.

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

For a damaged image, `acornfs repair-plan IMAGE` produces a read-only assessment
of candidate and manual recovery actions. It does not offer an apply flag and it
does not modify either member of the pair. Follow the preservation workflow in
[damaged-images.md](damaged-images.md) before investigating valuable media.

## Status and unmounting

```shell
acornfs status
acornfs unmount "$HOME/AcornFS/scsi0"
```

Unmounting causes the foreground mount command to exit. `Ctrl-C` in the mount
terminal also unmounts cleanly. A writable unmount is never lazy: AcornFS waits
for the daemon to flush, validate and remove its checkpoint before reporting
success. If that cannot be confirmed, the image remains subject to recovery and
must not be remounted read-write. If Nautilus keeps a read-only location busy,
close its window and retry, or detach it:

```shell
acornfs unmount --lazy "$HOME/AcornFS/scsi0"
```

Applications may hold multiple writable descriptors for one file. AcornFS uses
one shared per-inode buffer, so writes and truncation are visible through all
of those descriptors and `fsync` on any one commits their combined state. On a
graceful unmount, logout `SIGINT`, or normal FUSE-loop exit, any dirty buffers
left open by applications are committed before final validation. If that flush
fails, the recovery checkpoint is retained and the unmount is not reported as
clean.

Generate a support report that omits image contents and absolute paths:

```shell
acornfs diagnostics
acornfs diagnostics --json > acornfs-diagnostics.json
```

Desktop-created mountpoints use the persistent `sidebar` location by default.
To keep them entirely within the private login-session runtime directory, use:

```shell
acornfs config-mount-location runtime
```

This resolves to `$XDG_RUNTIME_DIR/acornfs/images`. The command also accepts an
absolute path, reports the current setting without an argument, and restores the
sidebar default with `--reset`. Manual `acornfs mount IMAGE MOUNTPOINT` commands
continue to use the supplied mountpoint. Changing the preference
does not relocate active images: AcornFS continues to recognise and reuse each
existing mount at its original path, while later images use the new root.

Desktop mount startup performs conservative housekeeping. Inactive fallback
logs and non-authoritative orphan checkpoint fragments are retained for 7 days;
completed repair audits without retained recovery state are retained for 90
days. Active logs, readable checkpoint manifests, failed audits, audits linked
to a retained checkpoint, symlinks, and unrecognised files are never removed by
automatic cleanup.

## Current limits

- Manual mounts are foreground processes. Nautilus actions use a collected transient systemd
  user service when the desktop session provides one, with a detached-process fallback.
- POSIX timestamp changes are accepted for application compatibility but are not persisted.
- All entries currently use the DAT file's modification time as their POSIX time.
- Unsafe, malformed, ambiguous, or non-ADFS pairs are rejected rather than repaired.
- Filename characters unavailable on POSIX are displayed with unambiguous Unicode glyphs.
