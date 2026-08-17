# AcornFS threat model

This model covers the amd64 Ubuntu/Nautilus deployment supported before the
first release. It should be reviewed whenever a new image format, writable
operation, IPC mechanism or privilege boundary is added.

## Assets and trust boundaries

AcornFS protects the host user's files, the integrity and confidentiality of
disk images, recovery checkpoints, mount records and preferences. DAT, DSC,
floppy and MMB contents, filenames, desktop URIs, FUSE requests and paths below
user-selected image directories are untrusted.

The logged-in user, the installed AcornFS/Oaknut code, the kernel FUSE driver
and the user's local systemd/Nautilus session are trusted. Root execution,
hostile access by another account to the user's session, remote URIs and a
compromised kernel or desktop session are outside the supported boundary.

## Principal threats and controls

| Threat | Impact | Current controls |
| --- | --- | --- |
| Malformed geometry, maps, catalogues or extents | crash, excessive work, out-of-image access or corruption | exact DSC parsing, content-driven format selection, bounded node/depth/cache sizes, complete pre-write validation and fuzz targets |
| Symlink, hard-link, rename or replacement races | validate one file and mutate another, or redirect state writes | canonical pair discovery, both members opened once and locked, device/inode revalidation, descriptor reads and checkpoints from locked handles, writable hard-link refusal and external-change signatures |
| Concurrent or interrupted writers | lost updates or partially written images | non-blocking exclusive pair locks, one writer, recovery checkpoint, sector transactions, fsync boundaries, rollback and post-write validation |
| Malicious FUSE caller input | namespace escape, invalid metadata or memory growth | inode-based operations, strict filename encoding/length rules, bounded buffers, kernel mount options `nodev,nosuid,noexec` and accurate unsupported-operation errors |
| Desktop URI or command injection | remote-file access or command execution | local `file:`/`acornfs:` schemes only, rejected authorities/query/fragment/NUL, argv-only subprocesses, escaped generated desktop fields and an allowlisted detached-child environment |
| Disclosure through diagnostics or UI | leak image data, unrelated paths or credentials | diagnostics use format/state summaries rather than image contents or absolute paths; detached children do not inherit unrelated environment secrets |
| Recovery/state tampering | rollback to attacker-controlled data or overwrite unrelated files | private per-user state roots, hashed image identities, create-only checkpoint files, atomic manifests and exact known-file cleanup |

Read-only mounting is the default. A writable mount is allowed only for the
narrow BeebSCSI old-map ADFS profile and only after validation succeeds. Safe
repair operations are separately allowlisted and always checkpointed.

## Residual risks and release gates

- AcornFS state-directory symlink and mount-root replacement tests remain a
  release gate; path ownership and containment must continue to be reviewed.
- Validation and repair are cooperatively cancellable and structurally bounded,
  but explicit wall-clock budgets for adversarial images remain to be defined.
- Native hardware interoperability, shutdown during active writes and desktop
  drag/drop/accessibility scenarios require manual acceptance testing.
- Oaknut is in-process and shares AcornFS privileges. Dependency pinning,
  vulnerability review and fuzzing are therefore part of the release process.

## Security test expectations

CI runs unit/property tests against corrupt and boundary images plus short
coverage-guided Atheris sessions for DSC parsing, URI handling and ADFS
map/catalogue validation. Crashes and hangs are defects even when an input is
otherwise unsupported. A security fix must add a regression test that does not
contain private image data.
