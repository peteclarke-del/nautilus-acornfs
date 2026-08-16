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

### Changed

- The initial supported host architecture is explicitly limited to amd64.

### Fixed

- Repair workflows now retain visible progress and completion details.
- Writable shutdown flushes dirty multi-handle buffers before final validation.

No public release has been cut yet. The first release will move these entries
under a dated version heading.
