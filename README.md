# Nautilus AcornFS

Nautilus AcornFS is an in-progress, user-space filesystem for Acorn disk
images. The first supported format will be paired BeebSCSI `DAT` and `DSC`
files. Images will be mounted through FUSE 3 and exposed to Nautilus through a
small extension, while the filesystem engine remains usable from any Linux
application.

The project is deliberately read-only while the image parser and validation
rules mature. See [TODO.md](TODO.md) for the roadmap.

## Current functionality

- Discover a matching BeebSCSI `DAT`/`DSC` pair from either member.
- Reject missing or ambiguous pairs.
- Parse and validate the geometry in a 22-byte BeebSCSI descriptor.
- Report pair metadata through `acornfs inspect`.
- Mount a validated ADFS image read-only through FUSE 3.
- Traverse directories and open files from Nautilus and other Linux applications.
- Mount, open, and unmount paired images from Nautilus context menus.

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

Right-click either member of a valid pair and select **Mount Acorn image**. The
image is mounted read-only in the user runtime directory and opened in Nautilus.
Right-click the DAT/DSC again to open or unmount it; **Unmount Acorn image** is
also available from the mounted folder's background menu.

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

Mounts are read-only by default and use `nodev`, `nosuid`, and `noexec`.
Experimental replacement and truncation of existing files is available from the
CLI with `acornfs mount --read-write`; it is not exposed in Nautilus yet.
Selection of either DAT or DSC is supported. The mountpoint must already exist
and be empty.

The initial development and CI container target is amd64. Native arm64 and
arm/v7 container builds remain on the roadmap.
