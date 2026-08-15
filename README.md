# Nautilus AcornFS

Nautilus AcornFS is an in-progress, user-space filesystem for Acorn disk
images. The first supported format will be paired BeebSCSI `DAT` and `DSC`
files. Images will be mounted through FUSE 3 and exposed to Nautilus through a
small extension, while the filesystem engine remains usable from any Linux
application.

Read-only mounting remains the default. Opt-in writable mounts use exclusive
pair locks, persistent pre-write checkpoints, external-change detection and
post-write ADFS validation. Acorn load/execute addresses, filetypes, lock state,
source filesystem and original paths are available as extended attributes. See
[TODO.md](TODO.md) for the remaining lifecycle and format work.

## Current functionality

- Discover a matching BeebSCSI `DAT`/`DSC` pair from either member.
- Reject missing or ambiguous pairs.
- Parse and validate the geometry in a 22-byte BeebSCSI descriptor.
- Report pair metadata through `acornfs inspect`.
- Mount a validated ADFS image read-only or read-write through FUSE 3.
- Traverse directories and open files from Nautilus and other Linux applications.
- Create, replace, truncate, rename and delete files and directories on writable mounts.
- Mount read-write, mount read-only, validate, recover and unmount from Nautilus context menus.
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

Install the per-user Nautilus extension and restart Files:

```shell
acornfs install-nautilus --restart
```

Right-click either member of a valid pair and choose **Mount Acorn image
read-write** or **Mount Acorn image read-only**. The mounted image opens in
Nautilus and appears in its sidebar. Right-click the DAT/DSC again to unmount it;
**Unmount Acorn image** is also available from the mounted root's background menu.

To remove the integration:

```shell
acornfs uninstall-nautilus --restart
```

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
