# Administrator guide

## Supported deployment

The initial support boundary is Ubuntu 24.04 LTS, GNOME/Nautilus 46 or later,
FUSE 3 and amd64. AcornFS runs entirely as the logged-in user. Do not install
setuid helpers, run Nautilus as root, grant broad `allow_other` access, or make
global `/etc/fuse.conf` changes for an ordinary deployment.

The release `.deb` documented in the root
[installation guide](../README.md#installation) is the preferred deployment.
It installs the command and Files integration system-wide through `apt`, uses
Ubuntu packages for platform dependencies, and vendors only the audited
pure-Python runtime unavailable from Ubuntu. It does not invoke `pip` or access
user home directories during package installation.

Remove an older per-user add-on before deploying the system package. A loader
under `~/.local/share/nautilus-python/extensions` takes precedence over the
system loader and can keep an old private environment active.

## Data and ownership

AcornFS writes only to the image opened by the user and these per-user locations:

| Data | Default location | Removal policy |
| --- | --- | --- |
| Preferences | `${XDG_CONFIG_HOME:-~/.config}/acornfs` | Preserve across upgrade/uninstall |
| Recovery checkpoints | `${XDG_STATE_HOME:-~/.local/state}/acornfs/recovery` | Preserve until resolved |
| Repair audits | `${XDG_STATE_HOME:-~/.local/state}/acornfs/repair-audits` | Preserve; completed disposable audits age out after 90 days |
| Runtime records/logs | `${XDG_RUNTIME_DIR}/acornfs` | Session-scoped; inactive logs age out after 7 days |
| Default mounts | `~/AcornFS Mounts` | Unmount before removal; do not recursively delete active roots |
| Desktop integration | `/usr/share` for `.deb`; `${XDG_DATA_HOME:-~/.local/share}` for add-on | Remove with its matching package or add-on uninstaller |

Persistent directories are private to the user. Back up preferences and state
together before a release-candidate upgrade. A checkpoint is not a general
backup: it exists to resolve one interrupted writable session.

Private JSON state is updated through a synced temporary file and atomic
replacement. If memory or disk space runs out before replacement, AcornFS keeps
the previous valid preference, mount record, manifest or audit and removes
the temporary file. A failed checkpoint payload copy is removed and the image
is not modified. Treat any reported state-write failure as actionable:
free space without deleting recovery data, then retry the original operation.

## Service lifecycle

Desktop mounts use collected transient systemd user services when available.
They receive `SIGINT` at logout or shutdown so dirty handles can flush before
the image is finalised. Inspect a session with:

```shell
acornfs status
systemctl --user list-units 'acornfs-mount-*.service'
journalctl --user --unit 'acornfs-mount-*.service'
```

Do not kill a writable daemon with `SIGKILL` during routine administration. If
a host failure does so, leave the image and checkpoint untouched and use the
documented recovery flow at the next login.

## Upgrade procedure

1. Unmount all images and confirm `acornfs status` is empty.
2. Resolve every pending recovery checkpoint; never discard one merely to make
   an upgrade proceed.
3. Back up the XDG configuration and state directories.
4. Install the new release with
   `sudo apt install ./nautilus-acornfs_VERSION_amd64.deb`.
5. Run `nautilus --quit`, open Files again and confirm `acornfs --help` and
   `acornfs status` succeed.
6. Validate a disposable known-good image read-only before enabling writes.

The automated package lifecycle smoke test force-reinstalls the wheel and proves
that preferences, checkpoint-shaped state and repair audits remain byte-for-byte
unchanged.

## Uninstall procedure

Unmount all images first, confirm `acornfs status` is empty, then run
`sudo apt remove nautilus-acornfs` and restart Files. Package removal deletes
the system command and desktop integration but retains user state. If the user later requests complete
erasure, first confirm that there is no pending recovery and identify the exact
per-user AcornFS directories; never use a broad recursive deletion rooted at
`$HOME`, an XDG root, or the mount parent.

## Diagnostics and incident handling

Use `acornfs diagnostics --json` rather than copying journals wholesale. The
export hashes paths and excludes image contents. For suspected malicious images
or unsafe filesystem behaviour, stop using the image, preserve it read-only,
record the AcornFS version and follow the private reporting route in
[SECURITY.md](../SECURITY.md).
