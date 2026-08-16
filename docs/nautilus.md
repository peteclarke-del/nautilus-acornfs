# Nautilus integration

## Install for the current user

Install the runtime and AcornFS first, then install the extension:

```shell
sudo apt install python3-nautilus gir1.2-nautilus-4.0 fuse3 libfuse3-dev pkg-config
python -m pip install -e '.[fuse]'
acornfs install-nautilus --restart
```

The installer writes one generated bootstrap to
`~/.local/share/nautilus-python/extensions/nautilus_acornfs.py`. It records the current
Python environment and source installation, allowing Nautilus's system Python
to load a development virtual environment. Re-run the installer after moving or
recreating that environment.

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

Each image receives a stable location under `~/AcornFS Mounts/IMAGE-HASH`.
GNOME deliberately exposes user FUSE mounts below the home directory in the
Files sidebar. Runtime locks and logs remain under `$XDG_RUNTIME_DIR/acornfs`.
The extension normally starts each mount as a collected transient systemd user
service. This keeps the daemon independent of Nautilus, records output in the
user journal, and sends `SIGINT` during logout or shutdown so FUSE can flush,
validate and remove a clean checkpoint. A detached-process fallback is retained
for desktop sessions without a systemd user manager. A dead FUSE endpoint is
detected and detached automatically before the next mount attempt.

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

The command refuses to remove a file that was not generated by AcornFS.
