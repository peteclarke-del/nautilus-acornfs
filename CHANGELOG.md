# Changelog

All notable changes to Nautilus AcornFS are recorded here. The project follows
[Semantic Versioning](https://semver.org/) and keeps unreleased work at the top.

## Unreleased

### Added

- Read-only and checkpointed read-write mounting of paired BeebSCSI DAT/DSC images.
- Nautilus integration for mounting, unmounting, validation, repair and image creation.
- Transactional ADFS file, directory and Acorn metadata mutations with recovery checkpoints.
- Explicit host-file import and export with portable Acorn `.inf` metadata sidecars.
- Deterministic amd64 performance budgets and bounded sequential large-file read-ahead.
- Privileged amd64 CI coverage for live writable FUSE, recovery and shutdown lifecycles.
- Conservative retention for inactive runtime logs, completed repair audits and orphan checkpoint fragments.
- Versioned validation JSON with a stable BeebSCSI old-map compatibility profile.
- amd64 memory stress budgets for 100,000 indexed nodes and an 8 MiB write buffer.
- The AcornFS-side shell-free Nautilus hand-off contract for future Acorn File Forge desktop launchers.
- Gettext foundations and translator guidance for Nautilus properties, menus, notifications and dialogs.

### Changed

- The initial supported host architecture is explicitly limited to amd64.
- Validation findings, repair plans/progress and known image-property values now use gettext.
- Desktop lifecycle, creation, recovery and preference errors now use gettext.
- Desktop-reachable pair, image and FUSE messages now complete gettext coverage.
- Existing mounts remain at, and are reused from, their original path after a location change.
- Compatible Acorn metadata changes are coalesced until a durability boundary.
- Successful mutations explicitly invalidate relevant kernel inode and entry caches.
- Remaining Acorn File Forge integration is explicitly deferred until the other backlog is complete.

### Fixed

- Repair workflows now retain visible progress and completion details.
- Writable shutdown flushes dirty multi-handle buffers before final validation.

No public release has been cut yet. The first release will move these entries
under a dated version heading.
