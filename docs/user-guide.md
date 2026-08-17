# User guide

## What AcornFS mounts

AcornFS exposes supported Acorn images as ordinary Linux folders. It currently
mounts paired BeebSCSI DAT/DSC old-map ADFS hard discs read-only or read-write,
and mounts ADFS S/M/L floppies, Acorn and Watford DFS SSD/DSD images, and
standard MMB containers read-only. Extended MMB, ROMFS and File Forge hand-off
remain future work.

Read-only is always the default. Only a BeebSCSI DAT/DSC pair that passes the
complete write-safety validation can be opened read-write.

## Install and enable Files integration

On the supported Ubuntu 24.04 amd64 host:

```shell
sudo apt install python3-venv fuse3 libfuse3-dev pkg-config \
  python3-nautilus gir1.2-nautilus-4.0 shared-mime-info \
  desktop-file-utils libnotify-bin zenity
python3 -m venv ~/.local/share/nautilus-acornfs/venv
git clone https://github.com/peteclarke-del/nautilus-acornfs.git
~/.local/share/nautilus-acornfs/venv/bin/pip install './nautilus-acornfs[fuse]'
~/.local/share/nautilus-acornfs/venv/bin/acornfs install-nautilus --restart
```

Run those commands from the directory containing the checkout, or use the
development instructions in the README. The installer changes only generated
files under the current user's XDG data directory. It never modifies images,
preferences, checkpoints or repair audits.

## Mount and browse

Keep a BeebSCSI DAT beside its same-basename DSC. In Files, right-click either
member and choose **Acorn FS Support → Open read-only**. For a writable hard
disc, choose **Open read-write**; AcornFS validates the image and creates a
checkpoint before accepting changes. The mount opens in Files and appears in
the sidebar.

Right-click an ADFS floppy, DFS image or MMB in the same way. Capability-driven
menus show only safe actions. MMB slots appear as numbered directories; DFS
catalogue prefixes are presentation directories and do not change the image.

Use **Acorn FS Support → Unmount** from the source image or mounted folder.
Unmount waits for writable data and metadata to flush and for final validation.
Close Files windows using the mount if a normal unmount reports that it is busy.

The terminal equivalents are:

```shell
mkdir -p "$HOME/AcornFS/scsi0"
acornfs mount /images/scsi0.dat "$HOME/AcornFS/scsi0"
acornfs status
acornfs unmount "$HOME/AcornFS/scsi0"
```

Add `--read-write` only for a supported, validated BeebSCSI pair.

## Validate, repair and recover

**Validate image** never changes the image. Findings distinguish fatal damage,
warnings and compatibility advice. **Repair image…** is offered only when the
format has supported repairs; it shows the proposed changes, creates a
checkpoint and verifies the complete image afterwards.

If a writable session is interrupted, the next attempt offers recovery. Restore
the checkpoint to return to the pre-mount image, or explicitly keep the current
image. Preserve both image members and recovery state until that decision is
made. See [damaged-images.md](damaged-images.md) for command examples and the
rollback guarantees.

## Create and transfer files

Right-click a writable local folder and choose **Acorn FS Support → Create
BeebSCSI image…**. The pair is published only after validation succeeds.

Files copied through a writable mount retain normal contents, but Linux tools do
not automatically understand every Acorn catalogue field. Use `acornfs
export-file` and `acornfs import-file` with `.inf` sidecars when metadata must
survive a host round trip. The precise mapping and filename limits are in
[metadata.md](metadata.md).

## Remove the desktop integration

Before removing the Python package, run:

```shell
acornfs uninstall-nautilus --restart
```

This removes only generated extension, MIME and desktop-handler files. It does
not remove images, mount-location preferences, recovery checkpoints or repair
audits. Do not manually delete recovery state for an unresolved writable mount.

## Get support safely

Create a privacy-safe diagnostic report with:

```shell
acornfs diagnostics --json > acornfs-diagnostics.json
```

Review it before attaching it to a report. Follow [SECURITY.md](../SECURITY.md)
for vulnerabilities and [admin-guide.md](admin-guide.md) for retained-state and
service troubleshooting.
