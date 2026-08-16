# Nautilus integration

## Install for the current user

Install the runtime and AcornFS first, then install the extension:

```shell
sudo apt install python3-nautilus gir1.2-nautilus-4.0 fuse3 libfuse3-dev pkg-config
python -m pip install -e '.[fuse]'
acornfs install-nautilus --restart
```

The installer writes a generated Nautilus bootstrap, shared-MIME package and
hidden desktop handler below `~/.local/share`, then refreshes the per-user MIME
and application databases. The bootstrap records the current Python environment
and source installation, allowing Nautilus's system Python to load a development
virtual environment. Re-run the installer after moving or recreating that
environment.

ADFS DAT files are recognised by their on-disc old-directory signature; AcornFS
does not register a generic `*.dat` glob. DSC files use their specific extension,
and the handler still validates the complete pair and descriptor before opening
anything. Double-click either recognised member to mount it read-only. Applications
may also open a local URI such as `acornfs:///path/to/scsi0.dat`; remote hosts and
other URI schemes are refused.

## Mount and unmount

Keep each DAT beside its matching DSC with the same basename. In Nautilus:

1. Right-click either file.
2. Open **Acorn FS Support** and select **Open read-only**, or choose **Open
   read-write** when changes should be possible.
3. Wait for the completion notification; the mounted root opens automatically.
4. Browse directories and open files normally. The image appears in the Files
   sidebar while it remains mounted.
5. Right-click the DAT/DSC and select **Acorn FS Support → Unmount**, or use the
   same submenu from the background menu at the mounted root.

All applicable AcornFS actions are kept in that single submenu. Select
**Validate image** to run a read-only ADFS structural check without
mounting or modifying the pair. A clean result is reported as a desktop
notification. When problems are found, a finite details dialog lists every
finding. Non-repairable reports have one **Close** button. When the complete
plan is eligible for low-risk repair, the dialog instead offers **Cancel** and
**Repair…**; Repair continues to the typed-filename confirmation without
running the initial validation again. The report window sizes itself to its
content, and repair completion or failure is shown in a separate compact dialog
with the audit or recovery detail. `acornfs validate IMAGE` prints the same
complete report.

Select **Repair image…** to review a complete eligible low-risk plan. AcornFS
requires the exact DAT filename in the confirmation dialog, creates a recovery
checkpoint, verifies the complete result and retains an audit. This can safely
restore a DAT that ends exactly at its ADFS boundary but omits a DSC-declared
reserved tail. Other geometry and allocation problems remain refused.

After confirmation, a determinate progress dialog remains visible throughout
planning, byte-counted recovery-checkpoint creation, repair application, complete
image verification and checkpoint finalisation. The progress dialog cannot be
cancelled once the transactional repair begins; interrupting it at an arbitrary
point could be misleading or unsafe. Completion and failure are still reported
in a separate result dialog with the audit or retained-checkpoint details.

Open **Properties** on either image member to see the detected old-map ADFS and
directory formats, compatibility profile, title, disc cycle ID, boot option,
DSC geometry, capacity, ADFS used/free space, reserved tail and current validation
state. Open **Properties** on an entry inside a mounted image to see its original
ADFS pathname and, for files, load address, execute address, RISC OS filetype and
locked state. Image properties perform complete read-only validation and may take
longer to appear for a very large or deeply populated image.

The same complete check runs before a read-write mount. Fatal geometry,
directory, map, or sector-allocation findings prevent the mount before its
checkpoint is created. Warnings and compatibility advice do not prevent access.

Desktop unmount detaches the sidebar entry immediately so an open Files window
cannot keep it busy. Existing handles finish in the background; the daemon then
flushes and validates the image before deleting its checkpoint.

Both modes carry `nodev`, `nosuid`, and `noexec`. A writable mount creates a
persistent pre-write checkpoint before appearing in Files, so a large non-reflink
image can take longer to mount.

## Create a BeebSCSI image

Right-click the background of a writable local folder, or right-click the folder
itself, then choose **Acorn FS Support → Create BeebSCSI image…**. The dialog
accepts a pair basename, an ADFS title of up to 12 printable ASCII characters,
and a capacity such as `20MB`. Leaving fields blank uses `scsi0`, `BLANK` and
`20MB`.

A determinate progress dialog covers filesystem creation, complete validation
and publication. AcornFS refuses case-insensitive DAT or DSC name collisions and
does not expose either final file until the newly created pair has passed
validation. If publishing the second member fails, the first is rolled back.
Creation is intentionally absent inside a mounted ADFS image.

**Open in Acorn File Forge…** now dispatches the canonical DAT/DSC pair through
an argv-only desktop launcher contract. AcornFS uses an installed
`acorn-file-forge` command, or `ACORN_FILE_FORGE_COMMAND` with optional complete
`{image}`, `{dat}`, and `{dsc}` argument placeholders. It never invokes a shell.
The end-to-end backlog item remains pending until File Forge provides the
corresponding helper or browser-session hand-off endpoint; without one, the
action explains what must be installed instead of pretending that opening the
web home page transferred the selected image. The helper must treat the source
pair as read-only input and upload or copy it into File Forge's private working
session rather than editing the original files directly.

## Interrupted writes and recovery

A clean unmount flushes pending data, validates the ADFS structure, and removes
the checkpoint. If the mount process crashes or validation fails, AcornFS keeps
the checkpoint and refuses another writable mount. First unmount any stale
sidebar entry, then right-click the DAT/DSC and select **Acorn FS Support →
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
short DAT/DSC commit boundary is deliberately non-cancellable so the pair cannot
be abandoned half-replaced. A separate result confirms whether cancellation or
recovery completed.

Each image receives a stable location under `~/AcornFS Mounts/IMAGE-HASH`.
GNOME deliberately exposes user FUSE mounts below the home directory in the
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
kills a writable daemon deliberately to prove that its pre-mount checkpoint can
restore and completely revalidate the original image.

## Troubleshooting

Only local files with an unambiguous matching partner receive the menu. If the
action is missing, check the pair with:

```shell
acornfs inspect /path/to/image.dat
```

Nautilus cannot reload Python extensions in place. After reinstalling or
updating the bootstrap, run `nautilus --quit` and reopen Files. Mount logs are
stored beside the runtime mountpoint under `$XDG_RUNTIME_DIR/acornfs`.

## Uninstall

```shell
acornfs uninstall-nautilus --restart
```

The command removes the generated extension, MIME definition and desktop handler,
refreshes their databases, and refuses to remove a file that was not generated by
AcornFS. Image data, recovery checkpoints and repair audits are never removed.
