# Nautilus AcornFS implementation backlog

## Objective

Build a safe userspace filesystem for Acorn disk images, starting with paired
BeebSCSI DAT and DSC files. Mounted images behave like ordinary folders in
Nautilus, terminals, editors and Linux file dialogs. A Nautilus extension
provides mount, unmount, creation, validation, repair, recovery, configuration
and properties actions while a FUSE daemon provides the actual filesystem.

A checked item means the behaviour is implemented and backed by automated tests
or explicit documentation. Hardware, distribution, live-desktop and release
acceptance items stay open until they have been exercised in that environment.

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
- [ ] Add the project licence.
- [x] Add the contribution guide and code style configuration.
- [x] Create packages for the filesystem core, FUSE adapter, command-line tools and Nautilus extension.
- [x] Add a reproducible development and test container for amd64.
- [ ] Add arm64 and arm/v7 development and test containers after the initial amd64 release.
- [x] Add CI for formatting, static analysis, unit tests, packaging and the amd64 container.
- [ ] Add privileged live-FUSE integration tests to CI.
- [x] Add generated test fixtures so private sample images are not required.
- [x] Define supported Ubuntu and GNOME/Nautilus versions.
- [x] Decide how reusable Acorn File Forge filesystem code will be extracted into a shared package.
- [x] Pin the shared Oaknut dependency used by Acorn File Forge.
- [x] Add a changelog and documented versioning/release policy.

## Phase 2: read-only BeebSCSI mounting

- [x] Implement DAT and DSC pair discovery using matching basenames.
- [x] Permit selection of either member and automatically locate its partner.
- [x] Reject ambiguous pairs and explain how to resolve them.
- [x] Parse and validate DSC geometry before opening the DAT image.
- [x] Cross-check descriptor geometry, DAT length and the ADFS free-space map.
- [x] Detect the ADFS format and target hardware characteristics.
- [x] Mount uncertain or damaged images read-only when safe traversal is still possible.
- [x] Implement FUSE lookup, getattr, open, read, release, opendir, readdir and statfs operations.
- [x] Traverse the complete ADFS directory tree.
- [x] Return stable inode identities for the life of a mount.
- [x] Report meaningful file sizes, ownership, permissions and timestamps.
- [x] Cache directory metadata and filesystem structures for the life of the mount.
- [x] Bound cache memory and invalidate entries predictably.
- [x] Provide clean handling for truncated images, broken directories, invalid maps and out-of-range sectors.
- [x] Add an `acornfs mount IMAGE MOUNTPOINT` command.
- [x] Add `acornfs unmount MOUNTPOINT` and mount-status commands.
- [x] Mount with `nosuid`, `nodev` and `noexec` by default.
- [x] Restrict a user mount to its owner unless explicitly configured otherwise.

## Phase 3: Acorn metadata mapping

- [x] Define extended attributes for `user.acorn.load`.
- [x] Define extended attributes for `user.acorn.execute`.
- [x] Define extended attributes for `user.acorn.filetype`.
- [x] Define extended attributes for `user.acorn.locked`.
- [x] Define extended attributes for source filesystem and original pathname information.
- [x] Implement getxattr and listxattr in read-only mode.
- [x] Map Acorn locked files to a sensible read-only POSIX presentation.
- [x] Keep optional `.inf` sidecars hidden and use extended attributes as the authoritative mounted representation.
- [x] Document lossy and lossless metadata mappings.
- [x] Add explicit metadata-aware import/export commands before offering generated `.inf` sidecars.

## Phase 4: safe write support

- [x] Add an explicit `--read-write` mount option while retaining read-only as the default.
- [x] Obtain exclusive locks on both DAT and DSC files for writable mounts.
- [x] Detect an already mounted or externally modified image.
- [x] Implement create, write, truncate, flush, fsync and release.
- [x] Implement mkdir and rmdir.
- [x] Implement rename and unlink.
- [x] Implement writable Acorn metadata through extended attributes.
- [x] Enforce ADFS filename, directory-entry and capacity restrictions.
- [x] Return clear POSIX errors for invalid names, full directories and insufficient space.
- [x] Never silently truncate or sanitise an invalid filename during a filesystem call.
- [x] Serialise mutations within each mounted image.
- [x] Maintain old-ADFS directory sequence fields correctly.
- [x] Advance the ADFS disc ID when required.
- [x] Rebuild and verify the free-space-map checksum after mutations.
- [x] Flush all pending metadata before reporting fsync or unmount success.
- [x] Prevent partial updates when an operation fails.
- [x] Add a write-ahead journal or equivalent recovery mechanism.
- [x] Store recovery state outside the mounted image and identify it by image identity.
- [x] Detect incomplete transactions on the next mount and offer recovery without modifying the original automatically.
- [x] Provide a mandatory pre-write checkpoint.
- [x] Use reflinks where available instead of blindly duplicating a complete large DAT file.
- [x] Add safe cancellation boundaries for long validation and recovery operations.
- [x] Add failure-injection coverage for every catalogue, data and metadata mutation class.
- [x] Define and test coherent per-inode buffering for multiple writable handles to one file.
- [x] Flush dirty open handles before graceful `SIGINT` shutdown finalises the image.
- [ ] Exercise an actual desktop logout and host shutdown while dirty writable handles remain open.

## Phase 5: Nautilus integration

- [x] Register MIME types for BeebSCSI DAT and DSC files without claiming unrelated generic DAT files.
- [x] Add a desktop application and URI handler for opening Acorn images.
- [x] Implement a Nautilus 4 extension using current model-based APIs.
- [x] Add `Mount Acorn image` to suitable DAT and DSC files.
- [x] Add `Mount read-only`.
- [x] Add `Unmount` for mounted images.
- [x] Add `Validate image`.
- [ ] Add `Open in Acorn File Forge`.
- [x] Add `Create BeebSCSI image` where appropriate.
- [x] Keep all AcornFS commands beneath one `Acorn FS Support` submenu.
- [x] Add desktop configuration for future mount locations.
- [x] Add a Nautilus properties model showing image type, geometry, ADFS format, title, capacity, free space, hardware profile, mount state and validation state.
- [x] Add file properties for load address, execute address, RISC OS filetype and lock state.
- [x] Make mounted images appear in Nautilus Places or the sidebar with a recognisable disk icon.
- [x] Provide desktop notifications for completed mounts, failed validation and recovery requirements.
- [ ] Ensure all actions are keyboard accessible and meet WCAG expectations.
- [ ] Test light mode, dark mode, narrow windows and 200 percent scaling.
- [ ] Test drag-and-drop, clipboard copy/move, trash/delete and atomic-save workflows in Nautilus.
- [ ] Make user-facing desktop strings translatable and add localisation guidance.
- [ ] Verify dialogs and notifications with a screen reader.

## Phase 6: lifecycle and desktop service

- [x] Run desktop mount daemons as collected transient systemd user services when available.
- [x] Track active mounts by canonical image path, device and inode.
- [x] Detect and detach stale FUSE endpoints before remounting.
- [x] Refuse to unmount while writes cannot be safely flushed.
- [x] Support graceful logout and shutdown handling through systemd `SIGINT` cleanup.
- [x] Record systemd-managed mount output in the user journal.
- [x] Add diagnostics that can be exported without including image contents.
- [x] Provide configurable per-user mount locations under `/run/user/$UID/acornfs`.
- [x] Persist mount-location preferences atomically with environment overrides and privacy-safe diagnostics.
- [x] Avoid requiring global `/etc/fuse.conf` changes for ordinary operation.
- [ ] Handle a changed mount-location preference while older images remain mounted.
- [ ] Define cleanup and retention for stale runtime logs, repair audits and abandoned checkpoints.

## Phase 7: validation and repair tooling

- [x] Add `acornfs inspect IMAGE` with machine-readable and human-readable output.
- [x] Validate geometry, directory sequences, map checksums, free-space extents and file extents.
- [x] Distinguish fatal errors, safe warnings and compatibility advice.
- [x] Add a dry-run repair plan.
- [x] Require explicit confirmation before applying any repair.
- [x] Create a checkpoint before every repair.
- [x] Verify the complete image after repair and retain an audit report.
- [x] Repair a DAT that safely omits only the DSC-declared reserved tail.
- [x] Show determinate byte-level progress throughout desktop repair.
- [x] Show explicit desktop completion or failure details after repair and recovery.
- [ ] Reuse Acorn File Forge compatibility checks for BBC, Master, Electron and BeebSCSI targets.
- [ ] Test images edited by the filesystem on real BeebSCSI hardware.
- [ ] Add a machine-readable compatibility profile/version to validation reports.
- [ ] Add automatic repairs only for further cases with complete rollback and hardware evidence.

## Phase 8: performance and concurrency

- [x] Benchmark initial mounting, root listing, deep traversal, large reads and small-file workloads.
- [ ] Benchmark large DAT images on Raspberry Pi 4 and Pi 5 hardware.
- [x] Keep one Oaknut mount and one eagerly built directory index for the life of each FUSE mount.
- [x] Add a bounded whole-file LRU cache and ranged reads for files larger than the cache limit.
- [x] Add sequential-read detection and bounded read-ahead for large files.
- [ ] Batch compatible metadata updates.
- [x] Define and test the concurrency model for simultaneous readers and a single writer.
- [x] Update or invalidate the userspace inode, directory and file caches after mutations.
- [ ] Notify the kernel to invalidate cached entries/data after mutations where zero timeouts are insufficient.
- [x] Ensure external image changes are detected rather than overwritten.
- [x] Record throughput and latency regressions in CI artefacts.
- [x] Establish amd64 performance budgets before the first release candidate.
- [ ] Profile memory use for maximum supported node counts and large open-write buffers.

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
- [ ] Define format detection precedence when one extension or container can hold multiple filesystems.
- [ ] Add capability-driven menu actions so unsupported formats never offer write or repair commands.

## Phase 10: packaging and release

- [ ] Produce Debian packages for supported Ubuntu releases.
- [ ] Package the FUSE daemon, command-line tools and Nautilus extension separately where useful.
- [ ] Declare FUSE 3, Python/runtime and Nautilus extension dependencies accurately.
- [ ] Add installation, upgrade and uninstall scripts that preserve user data.
- [x] Restart Nautilus only when explicitly requested and otherwise explain the required action.
- [ ] Add signed source archives and checksums.
- [ ] Add a complete administrator and user manual.
- [x] Document backup, recovery and damaged-image procedures.
- [x] Document limitations of Acorn-to-POSIX filename and metadata mapping.
- [ ] Publish a security policy and responsible disclosure route.
- [ ] Add clean-install, upgrade and uninstall smoke tests on each supported Ubuntu release.
- [ ] Produce reproducible release artefacts and a software bill of materials.
- [ ] Add release-candidate migration tests that preserve checkpoints, preferences and audits.
- [ ] Document support boundaries and a release-readiness checklist.

## Phase 11: security and robustness

- [x] Bound indexed node count, directory depth and file-cache memory for untrusted images.
- [x] Refuse ambiguous pairs, remote desktop URIs and unsafe writable geometry.
- [x] Keep lifecycle records and persistent preferences in private per-user directories.
- [ ] Write and review a threat model covering malicious images, paths, FUSE callers and desktop IPC.
- [ ] Add coverage-guided fuzzing for DSC parsing, ADFS map/catalogue validation and URI handling.
- [ ] Test symlink, hard-link, rename and time-of-check/time-of-use attacks around images, checkpoints and mount roots.
- [ ] Add dependency vulnerability and licence scanning to CI.
- [ ] Review subprocess environments, command construction and desktop file generation against injection.
- [ ] Define resource limits and timeouts for validation, properties and repair on adversarial images.
- [ ] Ensure logs, notifications and errors never disclose unrelated paths or image contents.

## Test matrix

- [x] Empty, lightly populated, nearly full and full images.
- [x] Valid DAT/DSC pairs across supported geometries.
- [x] Missing, mismatched, truncated and corrupt DSC files.
- [x] Truncated, oversized, sparse and corrupt DAT files.
- [ ] Old ADFS directory formats used by BBC, Master and Electron hardware.
- [ ] Newer ADFS formats where supported by the underlying library.
- [x] Deep trees, maximum old-directory entries and boundary-length names.
- [x] Locked files and every supported metadata combination.
- [x] Interrupted writes, daemon crashes and forced termination.
- [ ] Host shutdown during an active writable mount.
- [x] Concurrent readers and conflicting writers.
- [x] External modification while mounted.
- [x] Files larger than available image space.
- [ ] Nautilus drag and drop, rename, delete, copy and properties workflows.
- [x] Terminal and non-GNOME application access through the same mount.
- [ ] Ubuntu on amd64, arm64 and 32-bit arm/v7.
- [ ] Raspberry Pi 4 and Pi 5 native builds.
- [ ] Real BeebSCSI hardware after every write-path release candidate.
- [x] New-image creation, validation, collision refusal and partial-publication rollback.
- [x] Sidebar, runtime and custom mount-location preference resolution.
- [x] Filenames containing every supported display mapping and case-collision combination.
- [ ] Images stored on NFS, removable media, read-only media and filesystems without reflink support.
- [ ] Low-memory, low-disk-space and interrupted preference/audit/checkpoint writes.

## Initial release acceptance criteria

- [ ] Selecting either member of a valid DAT/DSC pair mounts the ADFS root read-only.
- [ ] Nautilus can traverse every valid directory and open every valid file.
- [ ] Terminal tools see the same hierarchy and contents.
- [x] Invalid geometry cannot reach a writable mount.
- [ ] Writable mounts preserve all existing files and Acorn metadata after create, edit, rename, move and delete operations.
- [x] Interrupted mutations are either rolled back or recoverable.
- [x] Unmount verifies and flushes the image before reporting success.
- [ ] An image edited through AcornFS works reliably on real BeebSCSI hardware.
- [x] No normal operation requires running the daemon or Nautilus as root.
- [x] Documentation covers installation, use, recovery, limitations and uninstalling.
- [ ] All supported Nautilus workflows pass the accessibility and visual test matrix.
- [ ] Clean install, upgrade and uninstall preserve images, preferences, checkpoints and audits.
- [ ] amd64 performance remains within the published release budgets.
- [ ] Security review and fuzzing find no unresolved release-blocking issue.

## Decisions to record before implementation

- [x] FUSE binding and implementation language.
- [x] Shared-library boundary with Acorn File Forge.
- [x] Transaction and recovery format.
- [x] POSIX timestamp policy for filesystems without equivalent timestamps.
- [x] Filename display, creation and case-insensitivity policy.
- [x] Extended-attribute and hidden-by-default `.inf` sidecar policy.
- [ ] MMB directory and write-semantics design.
- [x] Supported Ubuntu, GNOME and Nautilus versions.
- [x] Initial operating-system architecture (amd64; Raspberry Pi remains future work).
- [x] Default sidebar mount location and optional runtime/custom location policy.
- [ ] Support and compatibility policy for Oaknut private APIs across dependency updates.
