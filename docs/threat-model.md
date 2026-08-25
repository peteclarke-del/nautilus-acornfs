# AcornFS threat model

This model covers the amd64 Ubuntu/Nautilus deployment supported before the
first release. It should be reviewed whenever a new image format, writable
operation, IPC mechanism or privilege boundary is added.

## Assets and trust boundaries

AcornFS protects the host user's files, the integrity and confidentiality of
disk images, recovery checkpoints, mount records and preferences. DAT, DSC,
raw floppy, HFE, MMB and ROMFS contents, filenames, desktop URIs, FUSE requests and paths
below user-selected image directories are untrusted.

The logged-in user, the installed AcornFS/Oaknut code, the kernel FUSE driver
and the user's local systemd/Nautilus session are trusted. Root execution,
hostile access by another account to the user's session, remote URIs and a
compromised kernel or desktop session are outside the supported boundary.

## Principal threats and controls

| Threat | Impact | Current controls |
| --- | --- | --- |
| Malformed geometry, maps, catalogues or extents | crash, excessive work, out-of-image access or corruption | exact DSC parsing, content-driven format selection, 100,000-item/256-level/five-minute inspection budgets, full pre-write validation and fuzz targets |
| Symlink, hard-link, rename or replacement races | validate one file and mutate another, or redirect state writes | canonical source discovery, all members opened once and locked, device/inode revalidation, descriptor reads and checkpoints from locked handles, writable hard-link refusal, no-follow descriptor-relative state creation and external-change signatures |
| Concurrent or interrupted writers | lost updates or partially written images | shared reader and exclusive writer locks, one writer, persistent recovery checkpoints, sector or private whole-image transactions, fsync boundaries, rollback and post-write validation |
| Malicious FUSE caller input | namespace escape, invalid metadata or memory growth | inode-based operations, strict filename encoding/length rules, bounded buffers, kernel mount options `nodev,nosuid,noexec` and accurate unsupported-operation errors |
| Desktop URI or command injection | remote-file access or command execution | local `file:`/`acornfs:` schemes only, rejected authorities/query/fragment/NUL, argv-only subprocesses, escaped generated desktop fields and an allowlisted detached-child environment |
| Physical-floppy overwrite | accidental data loss, changing source, command injection or falsely reported success | responsive-device probe, suffix and drive allowlists, explicit overwrite confirmation, private stable snapshot, argv-only execution with a restricted environment, bounded error text and mandatory Greaseweazle verification |
| Malicious HFE conversion | resource exhaustion, stale-input mounting or destructive loss of protected tracks | v1/v3 signature and geometry checks, 64 MiB input limit, locked stable snapshot, bounded shell-free `gw` conversion, exact 100 percent sector requirement, standard Acorn geometry allowlist and refusal of lossy sector mounting |
| Disclosure through diagnostics or UI | leak image data, unrelated paths or credentials | diagnostics export bounded basenames and allowlisted mount flags; desktop errors, notifications and log excerpts redact absolute paths/control characters and are length-bounded; detached children do not inherit unrelated environment secrets |
| Recovery/state tampering or resource exhaustion | rollback to attacker-controlled data, overwrite unrelated files or publish partial state | private per-user roots, hashed identities, descriptor-relative create-only temporary files, full-write and fsync boundaries, atomic replacement, last-good-state preservation and exact partial-checkpoint cleanup |

Read-only mounting is the default. ADFS, DFS, standard HFE and MMB writable mounts are allowed
only after format-specific validation succeeds. ROMFS remains read-only. MMB
slot status is enforced independently, so a locked slot remains protected on a
writable container mount. HFE write-back retains the original container version,
file ownership, mode and extended attributes, then publishes through atomic
replacement. Safe repair operations are separately allowlisted and always checkpointed.

## Residual risks and release gates

- Native hardware interoperability, shutdown during active writes and desktop
  drag/drop/accessibility scenarios require manual acceptance testing.
- Oaknut is in-process and shares AcornFS privileges. Dependency pinning,
  the [private API upgrade gate](oaknut-compatibility.md), vulnerability review
  and fuzzing are therefore part of the release process.

## Security test expectations

CI runs unit/property tests against corrupt and boundary images plus short
coverage-guided Atheris sessions for DSC parsing, URI handling and ADFS
map/catalogue validation. Crashes and hangs are defects even when an input is
otherwise unsupported. A security fix must add a regression test that does not
contain private image data.
