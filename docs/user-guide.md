# User guide

## What AcornFS mounts

AcornFS exposes supported Acorn images as ordinary Linux folders. It currently
mounts paired BeebSCSI DAT/DSC old-map ADFS hard discs read-only or read-write,
and mounts ADFS S/M/L floppies, Acorn and Watford DFS SSD/DSD images, standard
MMB containers, and Acorn ROMFS paged-ROM images read-only. Extended MMB remains
future work. An installed native Acorn File Forge app can open supported source
pairs through the same submenu.

Read-only is always the default. Only a BeebSCSI DAT/DSC pair that passes the
complete write-safety validation can be opened read-write.

## Install and enable Files integration

On the supported Ubuntu 24.04 amd64 host:

```shell
sudo apt install python3-venv fuse3 libfuse3-dev pkg-config \
  python3-nautilus gir1.2-nautilus-4.0 shared-mime-info \
  desktop-file-utils libnotify-bin zenity
git clone https://github.com/peteclarke-del/nautilus-acornfs.git
python3 nautilus-acornfs/tools/user_install.py --restart install ./nautilus-acornfs
```

Run those commands from the directory containing the checkout, or use the
development instructions in the README. The installer creates a versioned
environment and `~/.local/bin/acornfs`, then changes only generated files under
the current user's XDG data directory. It never modifies images, preferences,
checkpoints or repair audits. Ensure `~/.local/bin` is on `PATH`, or invoke that
command by its full path.

## Mount and browse

Keep a BeebSCSI DAT beside its same-basename DSC. In Files, right-click either
member and choose **Acorn FS Support → Open read-only**. For a writable hard
disc, choose **Open read-write**; AcornFS validates the image and creates a
checkpoint before accepting changes. The mount opens in Files and appears in
the sidebar.

Right-click an ADFS floppy, DFS image, MMB or ROMFS image in the same way.
Capability-driven menus show only safe actions. MMB slots appear as numbered
directories; DFS catalogue prefixes are presentation directories and do not
change the image. A ROMFS flat catalogue appears at the mount root. Its names
remain case-sensitive, an embedded `/` is displayed as `∕`, and the Acorn
run-only flag appears in mounted-file Properties.

### Write an image with Greaseweazle

Physical-floppy writing is an optional integration. Install Greaseweazle from
its [official Linux instructions](https://github.com/keirf/greaseweazle/wiki/Software-Installation),
connect it, and check `gw info` succeeds in the same graphical login session.
AcornFS shows **Write to physical floppy…** only for `.ssd`, `.dsd`, `.adf`,
`.ads`, `.adm` and `.adl` files while that command and device are responsive.
Installing the Python package without connecting usable hardware does not add a
dead menu item.

Select drive A/B for a PC cable or unit 0-3 for a Shugart bus, then review the
final overwrite warning. The source is copied to a private stable snapshot
before `gw write` starts. Greaseweazle's normal verification remains enabled,
track progress and retries are shown, and success is displayed only after all
tracks are verified. Closing Files does not offer an unsafe mid-write cancel.
If a disconnect, write error or verification error occurs, treat the physical
floppy as incomplete and retry with known-good expendable media.

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

## Remove AcornFS

Unmount every image, retain the downloaded source archive or checkout containing
the lifecycle tool, then run:

```shell
python3 tools/user_install.py --restart uninstall
```

This refuses to proceed while a mount is active and removes only managed code,
its command, and generated extension, MIME and desktop-handler files. It does
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
