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
- Keep concurrent handles to one writable file coherent and flush dirty open handles on shutdown.
- Enforce and test deep-tree, 47-entry old-directory, 10-byte name and display-mapping boundaries.
- Enforce published amd64 latency, throughput and open-memory budgets in CI artefacts.
- Detect sequential large-file reads and use globally bounded per-handle read-ahead.
- Roll back and validate failed mutations while retaining crash-recovery checkpoints.
- Keep mount, validation, recovery and unmount actions together in one Nautilus submenu.
- Create an empty, validated BeebSCSI DAT/DSC pair from a writable Nautilus folder.
- Identify ADFS DAT content without claiming generic DAT files and open image or `acornfs:` URIs read-only.
- Cancel long validation and recovery work only at boundaries that leave images and checkpoints safe.
- Apply eligible low-risk catalogue repairs with confirmation, checkpointing and retained audits.
- Show live repair progress through checkpoint copying, mutation, verification and finalisation.
- Show image format, compatibility, geometry, capacity and validation details in Properties.
- Show Acorn load/execute addresses, filetype, lock state and original path for mounted entries.
- Run desktop mounts as collected systemd user services with graceful logout cleanup.
- Persist a per-user sidebar, runtime, or custom desktop mount location.
- Track mounted pairs by canonical path and DAT/DSC device/inode identity.
- Wait for writable flush and final validation before confirming unmount success.
- Export privacy-safe support information through `acornfs diagnostics --json`.

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

The suite includes real writable FUSE lifecycle tests for direct mounts,
Nautilus-style transient systemd user services and forced-daemon recovery.
Because some CI hosts expose `/dev/fuse` without granting mount permission,
ordinary test runs skip them unless explicitly enabled. Run them on a Linux
host permitted to create FUSE mounts with:

```shell
make test-live
```

Run the deterministic amd64 performance workload with `make benchmark`. It
writes a machine-readable report and enforces the first-RC budgets documented
in [docs/performance.md](docs/performance.md).

Install the per-user Nautilus extension, MIME types and desktop handler, then restart Files:

```shell
acornfs install-nautilus --restart
```

Right-click either member of a valid pair and open **Acorn FS Support**. Choose
**Open read-only**, **Open read-write**, **Validate image**, or **Repair image…**.
The mounted image opens in Nautilus and appears in its sidebar. The same submenu
offers **Unmount** on the DAT/DSC and from the mounted root's background menu,
keeping Acorn-specific actions out of Nautilus's top-level context menu.
The DAT/DSC **Properties** dialog includes an **Acorn disk image** section.
Properties for files inside an active mount include an **Acorn metadata** section.
Double-clicking a recognised image member opens the pair read-only. The same
handler accepts a local URI such as `acornfs:///path/to/scsi0.dat`.

Desktop mounts default to `~/AcornFS Mounts`, which gives Files the most reliable
sidebar presentation. Select a private session-runtime location, inspect the
effective setting, or restore the default with:

```shell
acornfs config-mount-location runtime
acornfs config-mount-location
acornfs config-mount-location --reset
```

An absolute directory is also accepted. `ACORNFS_MOUNT_ROOT` remains available
as a temporary environment override and takes precedence over the saved value.
The same setting is available as **Acorn FS Support → Mount location…** in Files.

To create an image, right-click the background of a writable local folder and
choose **Acorn FS Support → Create BeebSCSI image…**. Enter a basename, ADFS
title and capacity; blank fields use `scsi0`, `BLANK` and `20MB`. Creation shows
live progress and publishes the DAT/DSC pair only after complete validation. The
equivalent terminal command is:

```shell
acornfs create-beebscsi /path/to/folder --name scsi0 --title BLANK --capacity 20MB
```

To remove the extension, MIME types and desktop handler:

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

Generate a deterministic, read-only repair assessment with:

```shell
acornfs repair-plan /path/to/scsi0.dat
acornfs repair-plan --json /path/to/scsi0.dsc
```

The plan groups findings into candidate operations, records risk and identifies
steps that require a human decision. AcornFS can apply a complete plan only when
every action is a low-risk directory-length normalisation, empty-file catalogue
normalisation, or restoration of a DSC-declared tail omitted beyond the exact
ADFS boundary. First review the plan, then confirm with the exact DAT filename:

```shell
acornfs repair /path/to/scsi0.dat --confirm scsi0.dat
```

Every applied repair obtains the same exclusive pair locks as a writable mount,
creates a mandatory recovery checkpoint before mutation, performs sector-level
rollback on failure, completely revalidates the image, and retains a JSON audit
under `${XDG_STATE_HOME:-$HOME/.local/state}/acornfs/repair-audits`. Free-map,
geometry, unreadable-structure and overlapping-allocation plans remain refused.
See [docs/damaged-images.md](docs/damaged-images.md).

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

To capture environment and mount state for a bug report without including image
contents or absolute paths:

```shell
acornfs diagnostics --json > acornfs-diagnostics.json
```

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
