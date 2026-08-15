# Nautilus AcornFS implementation backlog

## Objective

Build a safe userspace filesystem for Acorn disk images, starting with paired BeebSCSI DAT and DSC files. Mounted images should behave like ordinary folders in Nautilus, terminals, editors and Linux file dialogs. A small Nautilus extension should provide convenient mount, unmount, validation and properties actions while a FUSE daemon provides the actual filesystem.

## Guiding principles

- Keep the filesystem engine independent of Nautilus and GNOME.
- Run as the current user without root privileges.
- Treat every image as untrusted input.
- Default uncertain or damaged images to read-only operation.
- Never silently repair an image merely because it was mounted.
- Keep image mutations transactional and recoverable.
- Preserve Acorn metadata even where POSIX has no direct equivalent.
- Share tested image logic with Acorn File Forge instead of duplicating it.
- Optimise for long-lived mounts. Do not reopen and reparse a large DAT image for every request.

## Phase 1: project foundation

- [x] Choose an implementation language and maintained FUSE 3 binding.
- [ ] Add the project licence, contribution guide and code style configuration.
- [x] Create packages for the filesystem core, FUSE adapter, command-line tools and Nautilus extension.
- [ ] Add reproducible development and test containers for amd64, arm64 and arm/v7. (amd64 only initially)
- [ ] Add CI for formatting, static analysis, unit tests, integration tests and architecture builds.
- [x] Add generated test fixtures so private sample images are not required.
- [x] Define supported Ubuntu and GNOME/Nautilus versions.
- [x] Decide how reusable Acorn File Forge filesystem code will be extracted into a shared package.

## Phase 2: read-only BeebSCSI mounting

- [x] Implement DAT and DSC pair discovery using matching basenames.
- [x] Permit selection of either member and automatically locate its partner.
- [x] Reject ambiguous pairs and explain how to resolve them.
- [x] Parse and validate DSC geometry before opening the DAT image.
- [ ] Cross-check descriptor geometry, DAT length and the ADFS free-space map.
- [ ] Detect the ADFS format and target hardware characteristics.
- [ ] Mount uncertain or damaged images read-only when safe traversal is still possible.
- [x] Implement FUSE lookup, getattr, open, read, release, opendir, readdir and statfs operations.
- [x] Traverse the complete ADFS directory tree.
- [x] Return stable inode identities for the life of a mount.
- [x] Report meaningful file sizes, ownership, permissions and timestamps.
- [x] Cache directory metadata and filesystem structures for the life of the mount.
- [x] Bound cache memory and invalidate entries predictably.
- [ ] Provide clean handling for truncated images, broken directories, invalid maps and out-of-range sectors.
- [x] Add an `acornfs mount IMAGE MOUNTPOINT` command.
- [x] Add `acornfs unmount MOUNTPOINT` and mount-status commands.
- [x] Mount with `nosuid`, `nodev` and `noexec` by default.
- [x] Restrict a user mount to its owner unless explicitly configured otherwise.

## Phase 3: Acorn metadata mapping

- [ ] Define extended attributes for `user.acorn.load`.
- [ ] Define extended attributes for `user.acorn.execute`.
- [ ] Define extended attributes for `user.acorn.filetype`.
- [ ] Define extended attributes for `user.acorn.locked`.
- [ ] Define extended attributes for source filesystem and original pathname information.
- [ ] Implement getxattr and listxattr in read-only mode.
- [ ] Map Acorn locked files to a sensible read-only POSIX presentation.
- [ ] Decide whether optional `.inf` sidecars should be exposed, generated on export, or hidden by default.
- [ ] Document lossy and lossless metadata mappings.

## Phase 4: safe write support

- [x] Add an explicit `--read-write` mount option while retaining read-only as the default.
- [x] Obtain exclusive locks on both DAT and DSC files for writable mounts.
- [ ] Detect an already mounted or externally modified image.
- [ ] Implement create, write, truncate, flush, fsync and release.
- [ ] Implement mkdir and rmdir.
- [ ] Implement rename and unlink.
- [ ] Implement setattr for writable Acorn metadata.
- [ ] Enforce ADFS filename, directory-entry and capacity restrictions.
- [ ] Return clear POSIX errors for invalid names, full directories and insufficient space.
- [ ] Never silently truncate or sanitise an invalid filename during a filesystem call.
- [ ] Serialise mutations within each mounted image.
- [ ] Maintain old-ADFS directory sequence fields correctly.
- [ ] Advance the ADFS disc ID when required.
- [ ] Rebuild and verify the free-space-map checksum after mutations.
- [ ] Flush all pending metadata before reporting fsync or unmount success.
- [ ] Prevent partial updates when an operation fails.
- [ ] Add a write-ahead journal or equivalent recovery mechanism.
- [ ] Store recovery state outside the mounted image and identify it by image identity.
- [ ] Detect incomplete transactions on the next mount and offer recovery without modifying the original automatically.
- [ ] Provide an optional pre-write checkpoint.
- [ ] Use reflinks or sparse copies where available instead of blindly duplicating a complete large DAT file.
- [ ] Add safe cancellation boundaries for long validation and recovery operations.

## Phase 5: Nautilus integration

- [ ] Register MIME types for BeebSCSI DAT and DSC files without claiming unrelated generic DAT files.
- [ ] Add a desktop application and URI handler for opening Acorn images.
- [x] Implement a Nautilus 4 extension using current model-based APIs.
- [x] Add `Mount Acorn image` to suitable DAT and DSC files.
- [ ] Add `Mount read-only`.
- [x] Add `Unmount` for mounted images.
- [ ] Add `Validate image`.
- [ ] Add `Open in Acorn File Forge`.
- [ ] Add `Create BeebSCSI image` where appropriate.
- [ ] Add a Nautilus properties model showing image type, geometry, ADFS format, title, capacity, free space, hardware profile and mount state.
- [ ] Add file properties for load address, execute address, RISC OS filetype and lock state.
- [x] Make mounted images appear in Nautilus Places or the sidebar with a recognisable disk icon.
- [ ] Provide desktop notifications for completed mounts, failed validation and recovery requirements.
- [ ] Ensure all actions are keyboard accessible and meet WCAG expectations.
- [ ] Test light mode, dark mode, narrow windows and 200 percent scaling.

## Phase 6: lifecycle and desktop service

- [ ] Add a systemd user service for mount-daemon lifecycle management.
- [ ] Track active mounts by canonical image path, device and inode.
- [ ] Clean up stale mountpoints after crashes.
- [ ] Refuse to unmount while writes cannot be safely flushed.
- [ ] Support graceful logout and shutdown handling.
- [ ] Add structured logs through the user journal.
- [ ] Add diagnostics that can be exported without including image contents.
- [ ] Provide configurable per-user mount locations under `/run/user/$UID/acornfs`.
- [ ] Avoid requiring global `/etc/fuse.conf` changes for ordinary operation.

## Phase 7: validation and repair tooling

- [ ] Add `acornfs inspect IMAGE` with machine-readable and human-readable output.
- [ ] Validate geometry, directory sequences, map checksums, free-space extents and file extents.
- [ ] Distinguish fatal errors, safe warnings and compatibility advice.
- [ ] Add a dry-run repair plan.
- [ ] Require explicit confirmation before applying any repair.
- [ ] Create a checkpoint before every repair.
- [ ] Verify the complete image after repair and retain an audit report.
- [ ] Reuse Acorn File Forge compatibility checks for BBC, Master, Electron and BeebSCSI targets.
- [ ] Test images edited by the filesystem on real BeebSCSI hardware.

## Phase 8: performance and concurrency

- [ ] Benchmark initial mounting, root listing, deep traversal, large reads and small-file workloads.
- [ ] Benchmark large DAT images on Raspberry Pi 4 and Pi 5 hardware.
- [ ] Avoid mounting or reparsing the ADFS image for each filesystem call.
- [ ] Add bounded read caching with sequential-read detection.
- [ ] Batch compatible metadata updates.
- [ ] Define and test the concurrency model for simultaneous readers and a single writer.
- [ ] Invalidate kernel and userspace caches after mutations.
- [ ] Ensure external image changes are detected rather than overwritten.
- [ ] Record throughput and latency regressions in CI artefacts.

## Phase 9: additional Acorn formats

- [ ] Generalise the mount engine around filesystem capabilities rather than filename extensions.
- [ ] Add ADFS floppy images.
- [ ] Add DFS SSD and DSD images, presenting DFS catalogue prefixes coherently.
- [ ] Decide how DFS pseudo-directories should map to POSIX directories without changing on-disk semantics.
- [ ] Add MMB read-only mounting with slots represented as directories.
- [ ] Design safe MMB slot replacement, insertion, ejection and access-mode semantics.
- [ ] Add ROMFS images.
- [ ] Consider read-only UEF and archive traversal after disk filesystems are stable.
- [ ] Keep unsupported operations disabled and return accurate errors for each format.

## Phase 10: packaging and release

- [ ] Produce Debian packages for supported Ubuntu releases.
- [ ] Package the FUSE daemon, command-line tools and Nautilus extension separately where useful.
- [ ] Declare FUSE 3, Python/runtime and Nautilus extension dependencies accurately.
- [ ] Add installation, upgrade and uninstall scripts that preserve user data.
- [ ] Restart Nautilus only when required and explain the action to the user.
- [ ] Add signed source archives and checksums.
- [ ] Add a complete administrator and user manual.
- [ ] Document backup, recovery and damaged-image procedures.
- [ ] Document limitations of Acorn-to-POSIX filename and metadata mapping.
- [ ] Publish a security policy and responsible disclosure route.

## Test matrix

- [ ] Empty, lightly populated, nearly full and full images.
- [ ] Valid DAT/DSC pairs across supported geometries.
- [ ] Missing, mismatched, truncated and corrupt DSC files.
- [ ] Truncated, oversized, sparse and corrupt DAT files.
- [ ] Old ADFS directory formats used by BBC, Master and Electron hardware.
- [ ] Newer ADFS formats where supported by the underlying library.
- [ ] Deep trees, maximum directory entries and boundary-length names.
- [ ] Locked files and every supported metadata combination.
- [ ] Interrupted writes, daemon crashes, forced termination and host shutdown.
- [ ] Concurrent readers and conflicting writers.
- [ ] External modification while mounted.
- [ ] Files larger than available image space.
- [ ] Nautilus drag and drop, rename, delete, copy and properties workflows.
- [ ] Terminal and non-GNOME application access through the same mount.
- [ ] Ubuntu on amd64, arm64 and 32-bit arm/v7.
- [ ] Raspberry Pi 4 and Pi 5 native builds.
- [ ] Real BeebSCSI hardware after every write-path release candidate.

## Initial release acceptance criteria

- [ ] Selecting either member of a valid DAT/DSC pair mounts the ADFS root read-only.
- [ ] Nautilus can traverse every valid directory and open every valid file.
- [ ] Terminal tools see the same hierarchy and contents.
- [ ] Invalid geometry cannot reach a writable mount.
- [ ] Writable mounts preserve all existing files and Acorn metadata after create, edit, rename, move and delete operations.
- [ ] Interrupted mutations are either rolled back or recoverable.
- [ ] Unmount verifies and flushes the image before reporting success.
- [ ] An image edited through AcornFS works reliably on real BeebSCSI hardware.
- [ ] No operation requires running the daemon or Nautilus as root.
- [ ] Documentation covers installation, use, recovery, limitations and uninstalling.

## Decisions to record before implementation

- [ ] FUSE binding and implementation language.
- [ ] Shared-library boundary with Acorn File Forge.
- [ ] Transaction and recovery format.
- [ ] POSIX timestamp policy for filesystems without equivalent timestamps.
- [ ] Filename mapping and case-sensitivity policy.
- [ ] Extended-attribute and optional `.inf` sidecar policy.
- [ ] MMB directory and write-semantics design.
- [ ] Supported Ubuntu, GNOME and Nautilus versions.
- [ ] Minimum supported Raspberry Pi and operating-system architecture.
