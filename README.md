# Nautilus AcornFS

Nautilus AcornFS is a userspace filesystem for Acorn disk images. It mounts
paired BeebSCSI `DAT`/`DSC` hard discs, standalone ADFS S through G+ floppies,
FileCore hard-disc images, DFS `SSD`/`DSD` images, standard and extended MMB
containers, and Acorn ROMFS paged-ROM images through FUSE 3. A small extension
integrates the mounted filesystems with Nautilus. Other Linux applications can
use the mounts without Nautilus.

Mounts are read-only by default. Every writable ADFS, DFS and MMB mount uses
exclusive image locks, persistent pre-write checkpoints, external-change
detection, structural validation before writing and validation again before a
clean unmount. Fatal findings refuse writable access before a checkpoint is
created. Each mutation also has a private before-image. Old-map ADFS records
only the affected sectors; New Map ADFS, DFS and MMB use a reflink of the image
when possible and a bounded full copy otherwise. A failed mutation is restored
and validated before the session may continue. ROMFS is always read-only.

Acorn load/execute addresses, filetypes, lock/run-only state, source filesystem
and original paths are available as extended attributes. See [TODO.md](TODO.md) for
the remaining lifecycle and format work. Release history and policy are in
[CHANGELOG.md](CHANGELOG.md) and [docs/releases.md](docs/releases.md).
The desktop walkthrough is in [docs/user-guide.md](docs/user-guide.md),
with the release acceptance matrix in
[docs/desktop-acceptance.md](docs/desktop-acceptance.md) and deployment and
retained-state guidance in
[docs/admin-guide.md](docs/admin-guide.md).

## Current functionality

- Discover a matching BeebSCSI `DAT`/`DSC` pair from either member.
- Detect the ADFS S/M/L/D/E/E+/F/F+/G/G+ floppy family from content
  and mount it read-only or read-write, including Big-directory long filenames.
- Mount content-valid standalone FileCore HDF/HD4 and unpaired raw ADFS hard
  discs read-only or read-write when physical CHS geometry is unavailable.
- Mount content-detected Acorn and Watford DFS SSD/DSD images read-only or
  read-write, exposing catalogue prefixes and both DSD sides coherently.
- Mount standard and extended MMB containers read-only or read-write, with
  formatted slots exposed as globally numbered directories and an eight-mount
  DFS cache. Mutations are confined to slots marked read-write by the MMB.
- Mount CRC-validated 8 KiB and 16 KiB Acorn ROMFS images read-only, preserving
  case-sensitive flat-catalogue names and run-only metadata.
- Reject missing or ambiguous pairs.
- Parse and validate the geometry in a 22-byte BeebSCSI descriptor.
- Report pair metadata through `acornfs inspect`.
- Validate geometry, maps, directories and used/free sector allocation with
  typed reports.
- Publish versioned validation JSON and a stable BeebSCSI old-map compatibility profile.
- Mount every supported disk image read-only or read-write through FUSE 3,
  except ROMFS, which remains read-only.
- Traverse directories and open files from Nautilus and other Linux applications.
- Create, replace, truncate, rename and delete files and directories on writable mounts.
- Keep concurrent handles to one writable file consistent and flush dirty open
  handles on shutdown.
- Coalesce compatible Acorn metadata changes and notify kernel caches after mutations.
- Enforce and test deep-tree, 47-entry old-directory, 10-byte name and
  display-mapping boundaries.
- Enforce published amd64 latency, throughput and open-memory budgets in CI artefacts.
- Detect sequential large-file reads and use globally bounded per-handle read-ahead.
- Roll back and validate failed mutations while retaining crash-recovery checkpoints.
- Keep mount, validation, recovery and unmount actions together in one Nautilus submenu.
- Hand image pairs to an installed Acorn File Forge desktop launcher without invoking a shell.
- Create an empty, validated BeebSCSI DAT/DSC pair from a writable Nautilus folder.
- Identify ADFS DAT content without claiming generic DAT files, and open image
  or `acornfs:` URIs read-only.
- Cancel long validation and recovery work only at boundaries that leave images
  and checkpoints safe.
- Apply eligible low-risk catalogue repairs with confirmation, checkpointing and retained audits.
- Show live repair progress through checkpoint copying, mutation, verification and finalisation.
- Show image format, compatibility, geometry, capacity and validation details in Properties.
- Show Acorn load/execute addresses, filetype, lock state and original path for mounted entries.
- Run desktop mounts as collected systemd user services with graceful logout cleanup.
- Persist a per-user sidebar, runtime, or custom desktop mount location.
- Reuse mounts at their original path after a location change and safely age disposable state.
- Track mounted pairs by canonical path and DAT/DSC device/inode identity.
- Wait for writable flush and final validation before confirming unmount success.
- Export privacy-safe support information through `acornfs diagnostics --json`.
- Import and export individual files with Acorn load, execution and lock metadata sidecars.
- Translate all desktop UI and desktop-reachable filesystem messages through gettext.
- Detect an installed, responsive Greaseweazle and offer confirmed, progress-reported,
  verified physical-floppy writes for its supported image suffixes.

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

The suite mounts either member of a BeebSCSI pair, traverses it with terminal
tools, and exercises writable data and Acorn metadata across validation and
remount. It also covers Nautilus-style transient systemd user services and
forced-daemon recovery.
Because some CI hosts expose `/dev/fuse` without granting mount permission,
ordinary test runs skip them unless explicitly enabled. A dedicated amd64 CI
job provisions and checks a usable kernel FUSE device before running the live
suite, so unavailable FUSE access cannot be reported as a successful CI run.
Run the same tests on a Linux host permitted to create FUSE mounts with:

```shell
make test-live
```

Run the deterministic amd64 performance workload with `make benchmark`. It
writes a machine-readable report and enforces the first-RC budgets documented
in [docs/performance.md](docs/performance.md).

`make package-smoke` builds a wheel, installs it with FUSE support through the
managed per-user installer, atomically upgrades it, removes it and verifies
throughout that preferences, recovery state and repair audits are unchanged.
CI runs the same lifecycle on the supported Ubuntu 24.04 amd64 host.

For a release build, install `.[release]` and run `make release` from a clean
tagged checkout. The command derives `SOURCE_DATE_EPOCH` from the commit, builds
the wheel and source archive twice, refuses differing output, generates a
validated reproducible CycloneDX SBOM for the full FUSE installation and
writes `build/release/SHA256SUMS`. Release signing remains a separate manual
gate until the project has an approved signing-key policy.

The source archive also contains a preservation-aware per-user lifecycle tool:

```shell
python3 tools/user_install.py install dist/nautilus_acornfs-0.1.0-py3-none-any.whl
python3 tools/user_install.py upgrade dist/nautilus_acornfs-0.1.0-py3-none-any.whl
python3 tools/user_install.py uninstall
```

It uses versioned environments below the user's XDG data directory, atomically
switches the current release, refuses to uninstall active mounts, and never
removes images, preferences, checkpoints or repair audits. Add `--restart`
before the action only when Files should restart immediately.

Install the per-user Nautilus extension, MIME types and desktop handler, then restart Files:

```shell
acornfs install-nautilus --restart
```

Right-click either member of a valid pair, a supported ADFS/DFS floppy, an MMB,
or a ROMFS image and open **Acorn FS Support**. Writable formats offer **Open
read-only**, **Open read-write** and **Validate image**. BeebSCSI pairs may also
offer **Repair image…** and **Open in Acorn File Forge…** when those actions are
available. ROMFS offers read-only opening and properties only.
When the `gw` command and a connected Greaseweazle both respond, compatible
`.ssd`, `.dsd`, `.adf`, `.ads`, `.adm` and `.adl` files additionally offer
**Write to physical floppy…**. The action is hidden otherwise.
The mounted image opens in Nautilus and appears in its sidebar. The same submenu
offers **Unmount** on the DAT/DSC and from the mounted root's background menu,
keeping Acorn-specific actions out of Nautilus's top-level context menu.
The DAT/DSC **Properties** dialog includes an **Acorn disk image** section.
Properties for files inside an active mount include an **Acorn metadata** section.
Double-clicking a recognised image member opens the pair read-only. The same
handler accepts a local URI such as `acornfs:///path/to/scsi0.dat`.

### Greaseweazle physical-floppy writing

Install current Greaseweazle software using its
[official Linux instructions](https://github.com/keirf/greaseweazle/wiki/Software-Installation),
ensure `gw` is available in the graphical session's `PATH`, connect the device,
and confirm it responds before restarting Files:

```shell
gw info
nautilus --quit
```

Right-click a compatible floppy image and choose **Acorn FS Support → Write to
physical floppy…**. Select PC drive `A` or `B`, or Shugart unit `0` through `3`.
AcornFS then requires overwrite confirmation, takes a private snapshot of the
source, and invokes `gw write` without a shell. The progress
dialog follows written tracks and verification retries. Success is reported
only after Greaseweazle says all tracks verified; cancellation is available
before the physical write starts, but not while a disk could be half-written.
Disconnects, command failures and verification failures produce an explicit
warning that the destination disk must not be trusted.

The software workflow is covered automatically, but real-device acceptance is
still open in [TODO.md](TODO.md). Use expendable media for initial testing and
compare it on the target Acorn hardware before relying on the result.

### Acorn File Forge hand-off

The File Forge action is shown only when the native application's
`acorn-file-forge` launcher is executable. Detection checks `PATH` and the
native installer's stable `~/.local/bin/acorn-file-forge` location, because
Files may not inherit the user's interactive-shell `PATH`. Its launcher receives
the canonical DAT and DSC paths as separate arguments and copies them into a
private working session before editing.

An equivalent installed launcher can be selected with
`ACORN_FILE_FORGE_COMMAND`. The executable must also be resolvable before the
menu action appears. The value is split into arguments and is never passed to a
shell. It may use `{image}`, `{dat}`, and `{dsc}` as whole-argument
placeholders; when it has no placeholders, DAT and DSC are appended:

```shell
export ACORN_FILE_FORGE_COMMAND='file-forge-client --image {dat} --descriptor {dsc}'
```

Set an override in the graphical login environment before starting Files.
Missing, malformed or non-executable launchers hide the action entirely. AcornFS
does not probe or upload to the browser service at `http://localhost:8666`.

Desktop mounts default to `~/AcornFS Mounts`, which gives Files the most reliable
sidebar presentation. Select a private session-runtime location, inspect the
effective setting, or restore the default with:

```shell
acornfs config-mount-location runtime
acornfs config-mount-location
acornfs config-mount-location --reset
```

An absolute directory is also accepted. `ACORNFS_MOUNT_ROOT` remains available
as a temporary environment override and takes precedence over the saved value.
The same setting is available as **Acorn FS Support → Mount location…** in Files.

To create an image, right-click the background of a writable local folder and
choose **Acorn FS Support → Create BeebSCSI image…**. Enter a basename, ADFS
title and capacity; blank fields use `scsi0`, `BLANK` and `20MB`. Creation shows
live progress and publishes the DAT/DSC pair only after full validation. The
equivalent terminal command is:

```shell
acornfs create-beebscsi /path/to/folder --name scsi0 --title BLANK --capacity 20MB
```

To remove the extension, MIME types and desktop handler:

```shell
acornfs uninstall-nautilus --restart
```

Transfer a file without losing its Acorn catalogue metadata:

```shell
acornfs export-file /path/to/scsi0.dat '$.DOCS.GUIDE' ./GUIDE
acornfs import-file /path/to/scsi0.dat ./GUIDE --directory '$.BACKUP'
```

Export refuses to overwrite either `GUIDE` or `GUIDE.inf`. Import automatically
uses a single case-insensitive matching `.inf`, validates its recorded length,
preflights image space, and commits the data and metadata as one checkpointed
image mutation. Use
`--sidecar PATH` to select one explicitly, `--ignore-sidecar` for neutral
metadata, or `--name NAME` to override the imported ADFS leaf name.

Nautilus interface chrome, validation findings, repair plans/progress and known
technical property values use gettext and fall back to English. Image-owned
names, stable finding/action codes and low-level third-party error details are
not translated. See
[docs/localisation.md](docs/localisation.md) for extracting messages, adding a
catalogue, packaging it, and completing the manual accessibility checks.

## Validate an image

Validation is read-only. It accepts any supported writable image, including
either member of a DAT/DSC pair:

```shell
acornfs validate /path/to/scsi0.dat
acornfs validate --json /path/to/scsi0.dsc
```

For old-map BeebSCSI pairs, the report checks DSC/DAT geometry, the ADFS map,
directory structures and every used and free sector extent. Other writable
formats run their filesystem-specific structural validator. Findings are classified as `FATAL`,
`WARNING`, or `ADVICE`; JSON includes stable finding codes, sector totals, and a
`safe_for_write` flag. Fatal findings prevent a read-write mount before its
recovery checkpoint is created. Warnings and compatibility advice do not block
mounting, although warnings make the validation command exit non-zero for
strict unattended checks. Validation does not repair or modify the image.

Validation, image properties and repair share an adversarial-input budget of
five minutes, 100,000 visited items and 256 directory levels. Exceeding any
limit stops safely with an explicit error; repair cancellation or expiry never
bypasses its checkpoint and audit rules.

Generate a deterministic, read-only repair assessment with:

```shell
acornfs repair-plan /path/to/scsi0.dat
acornfs repair-plan --json /path/to/scsi0.dsc
```

The plan groups findings into candidate operations, records risk and identifies
steps that require a human decision. AcornFS can apply a plan only when
every action is a low-risk directory-length normalisation, empty-file catalogue
normalisation, or restoration of a DSC-declared tail omitted beyond the exact
ADFS boundary. First review the plan, then confirm with the exact DAT filename:

```shell
acornfs repair /path/to/scsi0.dat --confirm scsi0.dat
```

Every applied repair obtains the same exclusive pair locks as a writable mount,
creates a mandatory recovery checkpoint before mutation, performs sector-level
rollback on failure, revalidates the full image, and retains a JSON audit
under `${XDG_STATE_HOME:-$HOME/.local/state}/acornfs/repair-audits`. Free-map,
geometry, unreadable-structure and overlapping-allocation plans remain refused.
See [docs/damaged-images.md](docs/damaged-images.md).

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

To capture environment and mount state for a bug report without including image
contents or absolute paths:

```shell
acornfs diagnostics --json > acornfs-diagnostics.json
```

Mounts are read-only by default and use `nodev`, `nosuid`, and `noexec`. Pass
`--read-write` for file and directory writes on ADFS, DFS and MMB images.
ROMFS rejects writable mounting. Selection of either DAT or DSC is supported.
The mountpoint must already exist and be empty.

The initial development and CI container target is amd64. Native arm64 and
arm/v7 container builds remain on the roadmap.

Images and desktop references are treated as untrusted input. See the
[security policy](SECURITY.md) and [threat model](docs/threat-model.md) for the
supported boundary, mitigations and remaining release gates. Maintainers can
run short amd64 coverage-guided parser checks with `make fuzz-smoke` after
installing the `fuzz` optional dependency.

AcornFS-owned runtime, configuration, recovery and audit directories are
created privately without following symbolic links. Desktop-visible external
errors are bounded and redact unrelated absolute paths; exported diagnostics
contain only bounded basenames, allowlisted mount flags and hashed identities.
Private state updates use one durable create-sync-replace-sync implementation:
short or interrupted writes are retried, while disk or memory exhaustion leaves
the last complete record intact and removes partial checkpoint data.

Debian package boundaries and exact Ubuntu runtime package names are documented
in [packaging/debian/README.md](packaging/debian/README.md). Actual `.deb`
production remains blocked until the project licence is selected and Oaknut has
a reviewed Debian packaging or vendoring route; AcornFS will not disguise those
requirements with a root-time `pip` download. Maintainers can run
`make debian-staging` to produce three disjoint, non-distributable amd64 package
roots and an ownership/dependency manifest under `build/debian-staging`.
The exact Oaknut pin and private-adapter upgrade gate are documented in
[docs/oaknut-compatibility.md](docs/oaknut-compatibility.md).

Writable mounts support BeebSCSI DAT/DSC pairs, standalone ADFS S through G+
floppies, FileCore and unpaired raw ADFS hard discs, Acorn and Watford DFS
SSD/DSD images, and standard or extended MMB containers. On an SSD, DFS
catalogue prefixes (`$`, `A`-`Z`)
appear as directories. On a DSD, drive directories `0` and `2` contain each
side's catalogue-prefix directories. These are presentation-only namespaces;
AcornFS does not create directory records that DFS cannot represent. Files
cannot be renamed between DSD sides. Other image types remain unsupported
rather than being guessed.

Standard and extended MMB containers expose only formatted slots, named by a
stable global slot number and catalogue label. Extended images may contain up
to 16 independently catalogued 511-slot extents; all declared extents are
validated and presented. Each slot contains the same DFS prefix directories as
an SSD. Files inside slots marked read-write may be created, replaced, renamed
and removed. Locked slots remain protected, and files cannot be moved between
slots. Slot insertion, replacement, ejection and access-mode changes are not
yet supported. See [docs/mmb.md](docs/mmb.md) for the namespace and limits.

Acorn ROMFS images are identified from their CRC-valid block chain rather than
their filename extension. Their flat catalogue is presented at the mount root;
names remain case-sensitive, and an on-disc `/` is displayed as `∕`. ROMFS is
read-only, and its distinct run-only flag is exposed as `user.acorn.run_only`.
