# User guide

## What AcornFS mounts

AcornFS exposes supported Acorn images as ordinary Linux folders. It currently
mounts paired BeebSCSI DAT/DSC images, ADFS S/M/L/D/E/E+/F/F+/G/G+ floppies,
standalone FileCore and unpaired raw ADFS hard discs, Acorn and Watford DFS
SSD/DSD images, and standard or extended MMB containers read-only or read-write.
Complete standard Acorn HFE v1 and HFEv3 DFS/ADFS floppies are also read-write.
Acorn ROMFS paged-ROM images remain read-only. An installed native
Acorn File Forge app can open supported source pairs through
the same submenu.

Read-only is always the default. A disk image is offered read-write only after
its format has been identified and its structural validation can protect the
write path. ROMFS never offers writable access.

## Install and enable Files integration

The Debian package is the recommended installation method on Ubuntu 24.04
amd64. Download the `.deb` and `SHA256SUMS` from the matching
[GitHub release](https://github.com/peteclarke-del/nautilus-acornfs/releases).
Replace `VERSION` with the downloaded release number:

```shell
cd ~/Downloads
sha256sum --ignore-missing --check SHA256SUMS
sudo apt update
sudo apt install ./nautilus-acornfs_VERSION_amd64.deb
nautilus --quit
```

Open Files again, then verify the command and desktop integration:

```shell
acornfs --help
acornfs status
test -f /usr/share/nautilus-python/extensions/nautilus_acornfs.py
```

The package uses normal Ubuntu dependencies and does not run `pip` during
installation. If `apt` cannot find `python3-pyfuse3`, enable Ubuntu's Universe
component, update the package lists and repeat the install:

```shell
sudo add-apt-repository universe
sudo apt update
```

Before migrating from the per-user add-on, unmount all images and run this from
its retained extracted directory:

```shell
python3 install.py --restart uninstall
```

This removes its per-user loader before the system package is installed and
retains images, preferences, checkpoints and repair audits. A release add-on
remains available for users who cannot install a system package; its bundled
`INSTALL.txt` contains the install, upgrade and removal procedure.

For an upgrade, first unmount every image and install the new `.deb` with the
same `sudo apt install ./nautilus-acornfs_VERSION_amd64.deb` command. Remove
AcornFS with `sudo apt remove nautilus-acornfs`. Both operations retain user
data. Restart Files after an upgrade or removal.

Open Files and right-click a supported image. One **Acorn FS Support** submenu
should appear.

For source development, clone the repository and follow the root README's
development section. Do not use an editable source install as the normal
desktop deployment.

## Mount and browse

Keep a BeebSCSI DAT beside its same-basename DSC. In Files, right-click either
member and choose **Acorn FS Support → Open read-only** or **Open read-write**.
The writable action validates the image and creates a checkpoint before
accepting changes. The mount opens in Files and appears in the sidebar.

Right-click an ADFS floppy, DFS image, HFE image, MMB or ROMFS image in the same way.
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

HFE mounting requires `gw` from the Greaseweazle host tools in the graphical
session's `PATH`. AcornFS accepts HFE v1 and HFEv3 only when every sector maps
to one supported standard Acorn DFS or ADFS geometry. Read-write mounts edit a
private raw workspace and atomically re-encode the same HFE version after final
validation. A recovery checkpoint protects the original container. Images with
missing sectors, copy protection or nonstandard tracks are deliberately not
mounted because a sector-level edit could not preserve those tracks.

DFS writes remain inside the selected catalogue prefix and DSD side. MMB writes
remain inside one slot marked read-write by its catalogue; locked slots are
readable but protected. AcornFS does not currently insert, eject, replace or
change the access status of whole MMB slots.

### Write an image with Greaseweazle

Physical-floppy writing is an optional integration. Install Greaseweazle from
its [official Linux instructions](https://github.com/keirf/greaseweazle/wiki/Software-Installation),
using `pipx` on the supported Ubuntu host:

```shell
sudo apt install gcc python3-pip python3-dev pipx
pipx ensurepath
pipx install git+https://github.com/keirf/greaseweazle@latest
```

Install the official udev rules, log out and back in if the login `PATH`
changed, connect the device, and check `gw info` succeeds in the same graphical
login session.
AcornFS shows **Write to physical floppy…** only for `.ssd`, `.dsd`, `.adf`,
`.ads`, `.adm`, `.adl` and signature-valid `.hfe` files while that command and
device are responsive.
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

For HFE v1 and HFEv3, AcornFS keeps the `.hfe` suffix on its stable snapshot and
lets Greaseweazle write the native track stream without a sector-format
override. This also permits a copy-protected or nonstandard HFE to be written
even though it cannot safely be exposed as a read-write filesystem.

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

Add `--read-write` for any supported ADFS, DFS, HFE or MMB image. ROMFS rejects the
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

Unmount every image, confirm `acornfs status` is empty, then run:

```shell
sudo apt remove nautilus-acornfs
nautilus --quit
```

This removes only packaged code, its command, and the system extension, MIME
and desktop-handler files. For the alternative add-on, run
`python3 install.py --restart uninstall` from its extracted directory instead.
Neither method removes images, mount-location preferences, recovery checkpoints
or repair audits. Do not manually delete recovery state for an unresolved
writable mount.

## Get support safely

Create a privacy-safe diagnostic report with:

```shell
acornfs diagnostics --json > acornfs-diagnostics.json
```

Review it before attaching it to a report. Follow [SECURITY.md](../SECURITY.md)
for vulnerabilities and [admin-guide.md](admin-guide.md) for retained-state and
service troubleshooting.
