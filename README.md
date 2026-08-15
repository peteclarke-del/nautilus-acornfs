# Nautilus AcornFS

Nautilus AcornFS is an in-progress, user-space filesystem for Acorn disk
images. The first supported format will be paired BeebSCSI `DAT` and `DSC`
files. Images will be mounted through FUSE 3 and exposed to Nautilus through a
small extension, while the filesystem engine remains usable from any Linux
application.

Read-only mounting remains the default. Opt-in writable mounts use exclusive
pair locks, persistent pre-write checkpoints, external-change detection and
complete pre-write and post-write ADFS integrity validation. Fatal geometry,
map, directory or allocation findings refuse writable access before a checkpoint
is created, while safe warnings and compatibility advice remain non-blocking.
Each mutation also uses a compact sector before-image, so a partially failed map
or catalogue update is rolled back and verified without sacrificing the rest of
the writable session. A successful logical mutation advances the old-ADFS disc
cycle ID exactly once and refreshes both map checksums; rollback restores the
previous ID. Oversized writes are rejected before their FUSE buffers consume the
unavailable capacity.
Acorn load/execute addresses, filetypes, lock state, source filesystem and
original paths are available as extended attributes. See [TODO.md](TODO.md) for
the remaining lifecycle and format work.

## Current functionality

- Discover a matching BeebSCSI `DAT`/`DSC` pair from either member.
- Reject missing or ambiguous pairs.
- Parse and validate the geometry in a 22-byte BeebSCSI descriptor.
- Report pair metadata through `acornfs inspect`.
- Validate geometry, maps, directories and used/free sector allocation with typed reports.
- Mount a validated ADFS image read-only or read-write through FUSE 3.
- Traverse directories and open files from Nautilus and other Linux applications.
- Create, replace, truncate, rename and delete files and directories on writable mounts.
- Roll back and validate failed mutations while retaining crash-recovery checkpoints.
- Mount read-write, mount read-only, validate, recover and unmount from Nautilus context menus.
- Show image format, compatibility, geometry, capacity and validation details in Properties.
- Show Acorn load/execute addresses, filetype, lock state and original path for mounted entries.
- Run desktop mounts as collected systemd user services with graceful logout cleanup.

## Development

Nautilus AcornFS requires Python 3.11 or later. To create a development
environment:

```shell
python3 -m venv .venv
. .venv/bin/activate
sudo apt install fuse3 libfuse3-dev pkg-config
python -m pip install -e '.[dev,fuse]'
pytest
```

The suite includes a real writable FUSE lifecycle test and runs it automatically
when `/dev/fuse` and `fusermount3` are available; restricted containers skip only
that test. Run it explicitly on a Linux host with:

```shell
pytest tests/test_live_fuse.py
```

Install the per-user Nautilus extension and restart Files:

```shell
acornfs install-nautilus --restart
```

Right-click either member of a valid pair and choose **Mount Acorn image
read-write** or **Mount Acorn image read-only**. The mounted image opens in
Nautilus and appears in its sidebar. Right-click the DAT/DSC again to unmount it;
**Unmount Acorn image** is also available from the mounted root's background menu.
The DAT/DSC **Properties** dialog includes an **Acorn disk image** section.
Properties for files inside an active mount include an **Acorn metadata** section.

To remove the integration:

```shell
acornfs uninstall-nautilus --restart
```

## Validate an image

Validation is read-only and accepts either member of the pair:

```shell
acornfs validate /path/to/scsi0.dat
acornfs validate --json /path/to/scsi0.dsc
```

The complete report checks DSC/DAT geometry, ADFS map and directory structures,
and all used and free sector extents. Findings are classified as `FATAL`,
`WARNING`, or `ADVICE`; JSON includes stable finding codes, sector totals, and a
`safe_for_write` flag. Fatal findings prevent a read-write mount before its
recovery checkpoint is created. Warnings and compatibility advice do not block
mounting, although warnings make the validation command exit non-zero for
strict unattended checks. Validation does not repair or modify the image.

For terminal use, create an empty mountpoint and mount either member manually:

```shell
mkdir -p "$HOME/AcornFS/scsi0"
acornfs mount /path/to/scsi0.dat "$HOME/AcornFS/scsi0"
```

The manual mount command remains in the foreground so failures stay visible. In
a second terminal:

```shell
nautilus "$HOME/AcornFS/scsi0"
acornfs status
acornfs unmount "$HOME/AcornFS/scsi0"
```

If Nautilus still holds the location open, close that window and retry. For a
read-only mount that must disappear immediately, use `acornfs unmount --lazy
MOUNTPOINT`; existing handles finish in the background.

Mounts are read-only by default and use `nodev`, `nosuid`, and `noexec`. Pass
`--read-write` for complete file and directory mutation support. Selection of
either DAT or DSC is supported. The mountpoint must already exist and be empty.

The initial development and CI container target is amd64. Native arm64 and
arm/v7 container builds remain on the roadmap.

The initial writable format is a BeebSCSI DAT/DSC hard-disc pair containing
old-map ADFS with old (Hugo) directories. The image metadata is compatible with
BBC Master BeebSCSI and RISC OS old-map ADFS access; the pair alone cannot prove
which physical host will be used. Other ADFS maps and image types remain
unsupported rather than being guessed.
