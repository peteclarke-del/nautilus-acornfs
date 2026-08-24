# Nautilus integration

## Install for the current user

Use the standalone release add-on described in the root
[installation guide](../README.md#installation). It installs AcornFS and the
Nautilus integration together. In summary, install the Ubuntu 24.04 amd64 host
dependencies, extract the downloaded add-on, and run this inside the extracted
directory:

```shell
python3 install.py --restart install
```

The managed installer creates a private Python environment, writes a stable
`~/.local/bin/acornfs` launcher, installs a generated Nautilus bootstrap,
shared-MIME package and hidden desktop handler below `~/.local/share`, then
refreshes the per-user MIME and application databases. It preserves images,
preferences, checkpoints and repair audits across upgrade and uninstall.

For an editable development checkout only, create and activate the virtual
environment documented in the root README, then run:

```shell
acornfs install-nautilus --restart
```

Re-run that command after moving or recreating the environment.

ADFS DAT files are recognised by their on-disc old-directory signature; AcornFS
does not register a generic `*.dat` glob. ADFS `.ads`, `.adm`, `.adl`, ambiguous
`.adf` floppy names and FileCore `.hdf`/`.hd4` names are registered for
discovery, but core content and geometry detection remains authoritative. DSC
files use their specific extension, and the handler still validates the full
pair and descriptor before opening anything. Double-click a supported image to
mount it read-only. Applications may also open a local URI such as
`acornfs:///path/to/scsi0.dat`; remote hosts and other URI schemes are refused.

## Mount and unmount

Keep each DAT beside its matching DSC with the same basename. In Nautilus:

1. Right-click either file.
2. Open **Acorn FS Support** and select **Open read-only**, or choose **Open
   read-write** for any supported ADFS, DFS or MMB image when changes should be
   possible. ROMFS remains read-only.
3. Follow the mount progress dialog. A finite success or error dialog is shown,
   and a successful mounted root opens automatically.
4. Browse directories and open files normally. The image appears in the Files
   sidebar while it remains mounted.
5. Right-click the DAT/DSC and select **Acorn FS Support → Unmount**, or use the
   same submenu from the background menu at the mounted root.

All applicable AcornFS actions are kept in that single submenu. Select
**Validate image** to run the format's read-only structural check without
mounting or modifying the image. A clean result is reported as a desktop
notification. When problems are found, a finite details dialog lists every
finding. Non-repairable reports have one **Close** button. When the full
plan is eligible for low-risk repair, the dialog instead offers **Cancel** and
**Repair…**; Repair continues to the typed-filename confirmation without
running the initial validation again. The report window sizes itself to its
content, and repair completion or failure is shown in a separate compact dialog
with the audit or recovery detail. `acornfs validate IMAGE` prints the same
report. Validation summaries, severity labels, finding explanations,
repair plans and progress text use the active AcornFS gettext catalogue; stable
finding codes and image paths remain unchanged for support and automation.

Select **Repair image…** to review an eligible low-risk plan. AcornFS
requires the exact DAT filename in the confirmation dialog, creates a recovery
checkpoint, verifies the result and retains an audit. This can safely
restore a DAT that ends exactly at its ADFS boundary but omits a DSC-declared
reserved tail. Other geometry and allocation problems remain refused.

After confirmation, a determinate progress dialog remains visible throughout
planning, byte-counted recovery-checkpoint creation, repair application, full
image verification and checkpoint finalisation. The progress dialog cannot be
cancelled once the transactional repair begins; interrupting it at an arbitrary
point could be misleading or unsafe. Completion and failure are still reported
in a separate result dialog with the audit or retained-checkpoint details.

Open **Properties** on an image to see its detected format, geometry, capacity,
space usage and validation state. Format-specific details include ADFS map and
directory formats, DFS sides, MMB slot counts and ROMFS capacity. Open
**Properties** on an entry inside a mounted image to see its original Acorn
pathname and available load, execute, filetype, lock and run-only metadata.
Image properties perform read-only validation and may take longer to appear for
a large or deeply populated image.

The same format-specific check runs before a read-write mount. Fatal findings
prevent the mount before its checkpoint is created. Warnings and compatibility
advice do not prevent access.

Desktop unmount detaches the sidebar entry immediately so an open Files window
cannot keep it busy. Existing handles finish in the background; the daemon then
flushes and validates the image before deleting its checkpoint.

Both modes carry `nodev`, `nosuid`, and `noexec`. A writable mount creates a
persistent pre-write checkpoint before appearing in Files, so a large non-reflink
image can take longer to mount.

Standalone ADFS S through G+, FileCore hard discs, DFS SSD/DSD images and MMB
containers expose **Open read-only**, **Open read-write**, **Validate image** and
recovery when a checkpoint is pending. D/E/F/G images retain New directories and
the `+` variants retain Big-directory long filenames. DFS SSD catalogue prefixes
appear as directories. DSD mounts add drive `0` and `2` directories so both
sides remain visible; cross-side moves are refused. MMB containers expose each
formatted slot as a globally numbered directory. Only slots marked read-write
accept changes, and cross-slot moves are refused. Repair and File Forge actions
remain limited to formats that implement those capabilities.

CRC-validated ROMFS images expose only **Open read-only** and image properties.
Their flat, case-sensitive catalogue is shown at the mount root.

## Create a BeebSCSI image

Right-click the background of a writable local folder, or right-click the folder
itself, then choose **Acorn FS Support → Create BeebSCSI image…**. The dialog
accepts a pair basename, an ADFS title of up to 12 printable ASCII characters,
and a capacity such as `20MB`. Leaving fields blank uses `scsi0`, `BLANK` and
`20MB`.

A determinate progress dialog covers filesystem creation, full validation
and publication. AcornFS refuses case-insensitive DAT or DSC name collisions and
does not expose either final file until the newly created pair has passed
validation. If publishing the second member fails, the first is rolled back.
Creation is intentionally absent inside a mounted ADFS image.

**Open in Acorn File Forge…** dispatches the canonical DAT/DSC pair through
the native application's argv-only launcher contract. The action is absent
unless `acorn-file-forge` is executable through `PATH` or at the native
installer's stable `~/.local/bin` location. An `ACORN_FILE_FORGE_COMMAND`
override is accepted only when its executable can also be resolved; optional
whole-argument `{image}`, `{dat}`, and `{dsc}` placeholders remain supported.
No command is passed to a shell. The native app treats the source pair as
read-only input and copies it into its private working session.

## Interrupted writes and recovery

A clean unmount flushes pending data, validates the filesystem, and removes
the checkpoint. If the mount process crashes or validation fails, AcornFS keeps
the checkpoint and refuses another writable mount. First unmount any stale
sidebar entry, then right-click the image and select **Acorn FS Support →
Resolve interrupted read-write mount…**. Choose either:

- **Restore image to the pre-mount checkpoint** to undo the interrupted session.
- **Keep the current image and discard the checkpoint** after independently
  checking that the current image is acceptable.

The equivalent terminal commands are `acornfs recover IMAGE`, `acornfs recover
IMAGE --restore`, and `acornfs recover IMAGE --discard`.

Validation and checkpoint restoration display a pulsing progress dialog. **Cancel
safely** stops validation between structural checks. During restoration it stops
while replacement files are still being staged, removes those temporary files,
and retains both the current image and checkpoint. After staging completes, the
short replacement commit boundary cannot be cancelled because the image must
not be abandoned half-replaced. A separate result confirms whether cancellation
or recovery completed.

Each image receives a stable location under `~/AcornFS Mounts/IMAGE-HASH`.
GNOME exposes user FUSE mounts below the home directory in the
Files sidebar. Runtime locks and logs remain under `$XDG_RUNTIME_DIR/acornfs`.
The extension normally starts each mount as a collected transient systemd user
service. This keeps the daemon independent of Nautilus, records output in the
user journal, and sends `SIGINT` during logout or shutdown so FUSE can flush,
validate and remove a clean checkpoint. A detached-process fallback is retained
for desktop sessions without a systemd user manager. A dead FUSE endpoint is
detected and detached automatically before the next mount attempt.

The mount location is a persistent per-user preference. The default `sidebar`
mode uses `~/AcornFS Mounts`; `runtime` uses the private session path
`$XDG_RUNTIME_DIR/acornfs/images` (normally `/run/user/$UID/acornfs/images`),
and an absolute custom directory is also supported:

```shell
acornfs config-mount-location runtime
acornfs config-mount-location /srv/acorn-mounts
acornfs config-mount-location --reset
```

Use `acornfs config-mount-location` without an argument to inspect the effective
mode. Runtime mounts are removed with the login session and may be less prominent
in the Files sidebar than the default home-directory location. The preference is
stored privately under `${XDG_CONFIG_HOME:-$HOME/.config}/acornfs`; the
`ACORNFS_MOUNT_ROOT` environment variable is a temporary higher-priority override.
The grouped **Acorn FS Support → Mount location…** action exposes the same
setting without requiring a terminal and applies it to future mounts.

The opt-in live-FUSE suite exercises this exact transient-service path and also
terminates a writable daemon to verify that its pre-mount checkpoint can
restore and revalidate the full original image. It also exercises the
copy-in, copy-out, move, permanent-delete and temporary-file replacement
patterns used beneath common Files and editor workflows. Drag-and-drop, trash,
visual layout, keyboard-only use and screen-reader behaviour still require the
supported GNOME session matrix in
[desktop-acceptance.md](desktop-acceptance.md).

## Troubleshooting

Only local files whose content resolves to a supported format receive the menu.
DAT and DSC images additionally require an unambiguous matching partner. If the
action is missing, inspect the image with:

```shell
acornfs inspect /path/to/image.dat
```

Nautilus cannot reload Python extensions in place. After reinstalling or
updating the bootstrap, run `nautilus --quit` and reopen Files. Mount logs are
stored beside the runtime mountpoint under `$XDG_RUNTIME_DIR/acornfs`.

Greaseweazle detection never blocks menu construction. Files checks for the
installed command and an accessible Greaseweazle identity under
`/dev/serial/by-id`; it does not start `gw info` from the menu provider. The
write workflow performs the authoritative `gw info` check before it presents
destructive controls.

The physical-write workflow detects usable drives before showing its selector.
It reports progress while `gw rpm` checks for index pulses from the inserted
destination floppy, using PC-bus identifiers first and Shugart identifiers only
when necessary. Only responding drives are listed. A timed-out probe triggers a
controller reset to deselect the drive and stop its motor.

Context-menu discovery uses registered image filename extensions and does not
open the image. The selected mount, validation, repair or physical-write command
performs full content checks before it can modify anything. Content-recognisable
images with unrelated extensions remain available through the `acornfs` CLI but
do not receive a Nautilus context menu.

## Uninstall

For a managed release installation, enter a retained extracted add-on directory
and run:

```shell
python3 install.py --restart uninstall
```

For an editable development installation, run
`acornfs uninstall-nautilus --restart` instead. Both paths remove the generated
extension, MIME definition and desktop handler, refresh their databases, and
refuse to remove files not generated by AcornFS. Image data, preferences,
recovery checkpoints and repair audits are never removed.
