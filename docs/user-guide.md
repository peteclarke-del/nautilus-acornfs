# User guide

## What AcornFS mounts

AcornFS exposes supported Acorn images as ordinary Linux folders. It currently
mounts paired BeebSCSI DAT/DSC images, ADFS S/M/L/D/E/E+/F/F+/G/G+ floppies,
standalone FileCore and unpaired raw ADFS hard discs, Acorn and Watford DFS
SSD/DSD images, and standard or extended MMB containers read-only or read-write.
Acorn ROMFS paged-ROM images remain read-only. An installed native
Acorn File Forge app can open supported source pairs through
the same submenu.

Read-only is always the default. A disk image is offered read-write only after
its format has been identified and its structural validation can protect the
write path. ROMFS never offers writable access.

## Install and enable Files integration

The release add-on is the recommended installation method. On the supported
Ubuntu 24.04 amd64 host, install its prerequisites first:

```shell
sudo apt update
sudo apt install --no-install-recommends \
  python3-venv python3-dev build-essential pkg-config \
  fuse3 libfuse3-dev \
  python3-nautilus gir1.2-nautilus-4.0 \
  shared-mime-info desktop-file-utils libnotify-bin zenity unzip
```

Download the add-on ZIP and `SHA256SUMS` from the matching
[GitHub release](https://github.com/peteclarke-del/nautilus-acornfs/releases).
Keep both files in the same directory, then verify, extract and install the
add-on. Replace `VERSION` with the downloaded release number:

```shell
cd ~/Downloads
sha256sum --ignore-missing --check SHA256SUMS
mkdir -p nautilus-acornfs-addon-VERSION
unzip nautilus-acornfs-addon-VERSION.zip -d nautilus-acornfs-addon-VERSION
cd nautilus-acornfs-addon-VERSION
python3 install.py --restart install
```

The installer uses the single wheel shipped beside it and downloads pinned
Python dependencies into a private versioned environment. It creates
`~/.local/bin/acornfs` and generated desktop files below `~/.local/share`; it
does not modify Ubuntu's system Python. Internet access is required during
installation. If the command is not found in a new terminal, add
`~/.local/bin` to `PATH` or use its absolute path.

Verify the installation before opening an image:

```shell
~/.local/bin/acornfs --help
~/.local/bin/acornfs status
test -f ~/.local/share/nautilus-python/extensions/nautilus_acornfs.py
```

Open Files and right-click a supported image. One **Acorn FS Support** submenu
should appear. If Files was not restarted, run `nautilus --quit` and open it
again.

For an upgrade, first unmount every image, extract the new add-on release and
run `python3 install.py --restart upgrade` from its directory. The installer
stages the replacement separately and changes the active release only after it
has installed and validated successfully.

For source development, clone the repository and follow the root README's
development section. Do not use an editable source install as the normal
desktop deployment.

## Mount and browse

Keep a BeebSCSI DAT beside its same-basename DSC. In Files, right-click either
member and choose **Acorn FS Support → Open read-only** or **Open read-write**.
The writable action validates the image and creates a checkpoint before
accepting changes. The mount opens in Files and appears in the sidebar.

Right-click an ADFS floppy, DFS image, MMB or ROMFS image in the same way.
Capability-driven menus show only safe actions. MMB slots appear as numbered
directories; DFS catalogue prefixes are presentation directories and do not
change the image. Extended MMB slots retain one global number across all
declared extents. Newer ADFS `+` images retain Big-directory long filenames,
including on writable mounts. A ROMFS flat catalogue appears
at the mount root. Its names remain case-sensitive, an embedded `/` is displayed
as `∕`, and the Acorn run-only flag appears in mounted-file Properties.

Standalone `.hdf`/`.hd4` FileCore images and content-valid raw ADFS hard discs
without a DSC are also writable. Their Properties show logical map details and
state that physical CHS is unavailable. They offer validation and recovery but
not descriptor-specific repair or File Forge actions.

DFS writes remain inside the selected catalogue prefix and DSD side. MMB writes
remain inside one slot marked read-write by its catalogue; locked slots are
readable but protected. AcornFS does not currently insert, eject, replace or
change the access status of whole MMB slots.

### Write an image with Greaseweazle

Physical-floppy writing is an optional integration. Install Greaseweazle from
its [official Linux instructions](https://github.com/keirf/greaseweazle/wiki/Software-Installation),
connect it, and check `gw info` succeeds in the same graphical login session.
AcornFS shows **Write to physical floppy…** only for `.ssd`, `.dsd`, `.adf`,
`.ads`, `.adm` and `.adl` files while that command and device are responsive.
Installing the Python package without connecting usable hardware does not add a
dead menu item.

Files checks the installed command and the Greaseweazle udev serial identity
without starting a process, so the action appears on the first right-click. The
write workflow runs `gw info` before continuing and refuses to present
destructive controls if the device does not respond.

Insert the destination floppy before continuing. AcornFS displays progress
while it checks the configured bus for index pulses, then offers only responding
drives in the selector. Detection briefly starts each candidate drive's motor
but does not read or write disk data. A timed-out probe resets Greaseweazle so
the drive is deselected and its motor is stopped.

Before writing, AcornFS identifies the actual Acorn filesystem and geometry and
passes the corresponding `acorn.adfs.*` or `acorn.dfs.*` format explicitly.
This is essential for `.adf`, which other systems also use for unrelated disk
formats. Files with an unsupported size or non-Acorn content are refused before
the physical write begins.

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

Add `--read-write` for any supported ADFS, DFS or MMB image. ROMFS rejects the
option.

## Validate, repair and recover

**Validate image** never changes the image. Findings distinguish fatal damage,
warnings and compatibility advice. **Repair image…** is offered only when the
format has supported repairs; it shows the proposed changes, creates a
checkpoint and verifies the full image afterwards.

If a writable session is interrupted, the next attempt offers recovery. Restore
the checkpoint to return to the pre-mount image, or keep the current image.
Preserve the image, any companion descriptor and recovery state until that
decision is made. See [damaged-images.md](damaged-images.md) for command examples
and the rollback guarantees.

## Create and transfer files

Right-click a writable local folder and choose **Acorn FS Support → Create
BeebSCSI image…**. The pair is published only after validation succeeds.

Files copied through a writable mount retain their contents, but Linux tools do
not automatically understand every Acorn catalogue field. Use `acornfs
export-file` and `acornfs import-file` with `.inf` sidecars when metadata must
survive a host round trip. The precise mapping and filename limits are in
[metadata.md](metadata.md).

## Remove AcornFS

Unmount every image, enter any retained extracted add-on directory, then run:

```shell
python3 install.py --restart uninstall
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
