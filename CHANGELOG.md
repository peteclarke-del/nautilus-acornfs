# Changelog

All notable changes to Nautilus AcornFS are recorded here. The project follows
[Semantic Versioning](https://semver.org/) and keeps unreleased work at the top.

## Unreleased

### Added

- Protected read-write mounting for standalone ADFS Old/New/Big directory
  floppies, FileCore and raw ADFS hard discs, DFS SSD/DSD images, and files
  inside read-write MMB slots. ROMFS remains read-only.
- Shared and exclusive locking for standalone images, hard-link refusal,
  external-change detection, persistent single-image recovery checkpoints and
  private per-operation whole-image rollback with reflink and bounded-copy paths.
- Format-specific validation profiles and recovery actions for writable
  standalone images and New Map DAT/DSC pairs.
- Privileged live-FUSE acceptance for selecting either BeebSCSI pair member,
  terminal traversal and post-remount writable data/metadata preservation.
- Deterministic amd64 Debian staging for separate core, FUSE and Nautilus
  package roots, with disjoint ownership, dependency and desktop-asset checks.
- Mounting and properties for ADFS D/E/E+/F/F+/G/G+ floppies and
  standalone FileCore/unpaired raw hard discs, including content-refined
  same-size variants and Big-directory long names.
- Read-only and checkpointed read-write mounting of paired BeebSCSI DAT/DSC
  images.
- Nautilus integration for mounting, unmounting, validation, repair and image
  creation.
- Transactional ADFS file, directory and Acorn metadata mutations with recovery
  checkpoints.
- Explicit host-file import and export with portable Acorn `.inf` metadata sidecars.
- Deterministic amd64 performance budgets and bounded sequential large-file read-ahead.
- Privileged amd64 CI coverage for live writable FUSE, recovery and shutdown lifecycles.
- Conservative retention for inactive runtime logs, completed repair audits and
  orphan checkpoint fragments.
- Versioned validation JSON with a stable BeebSCSI old-map compatibility profile.
- amd64 memory stress budgets for 100,000 indexed nodes and an 8 MiB write buffer.
- Content-detected mounting of standalone ADFS S, M and L floppy images.
- Content-detected Acorn and Watford DFS SSD/DSD mounting, with
  catalogue-prefix directories and both DSD sides exposed in one namespace.
- Content-detected standard MMB mounting, with formatted slots exposed
  as labelled directories through a bounded lazy DFS-mount cache.
- Extended MMB mounting across up to 16 independently validated
  extents, with global slot numbering and capacity properties.
- CRC-validated read-only Acorn ROMFS mounting with case-sensitive flat
  catalogues, load/execute metadata, run-only state and image properties.
- The AcornFS-side shell-free Nautilus hand-off contract for future Acorn File
  Forge desktop launchers.
- Gettext foundations and translator guidance for Nautilus properties, menus,
  notifications and dialogs.
- Ubuntu 24.04 amd64 wheel lifecycle smoke coverage for clean installation,
  forced upgrade and uninstall with retained user-state verification.
- User, administrator, security-reporting and release-readiness guides, plus a
  defined Debian split-package and dependency contract.
- A consistent Oaknut dependency set, preventing pip from combining the
  pinned `oaknut-disc` command package with untested newer filesystem modules.
- An amd64 threat model and coverage-guided fuzz targets for descriptors,
  desktop URIs and ADFS map/catalogue validation.
- amd64 dependency vulnerability auditing and licence-inventory artefacts in CI.
- Shared five-minute, 100,000-item and 256-level budgets for validation,
  properties and repair of untrusted images.
- Fault-injection coverage for low-memory, disk-full, interrupted and short
  private-state and checkpoint writes.
- A documented exact-family Oaknut private-API compatibility and upgrade gate.
- Reproducible wheel/source builds with a validated CycloneDX SBOM, unsigned
  SHA-256 manifest and retained amd64 CI artefacts.
- A transactional per-user install, upgrade and uninstall tool that keeps
  images, preferences, checkpoints and repair audits outside its removal scope.

### Changed

- Reviewed project documentation for consistent terminology, concise
  operational guidance and current support boundaries.
- Harden desktop accessibility metadata and destructive dialog button
  contracts, document the GNOME acceptance matrix, and cover copy, move,
  delete and atomic-save workflows through live FUSE.
- The File Forge menu action is offered only when the native or explicitly
  configured launcher executable is detected as installed.
- The Oaknut dependency family is aligned with Acorn File Forge at 12.15.1,
  including the ROMFS content-detection and traversal plugin.
- The initial supported host architecture is limited to amd64.
- Validation findings, repair plans/progress and known image-property values now use gettext.
- Desktop lifecycle, creation, recovery and preference errors now use gettext.
- Desktop-reachable pair, image and FUSE messages now complete gettext coverage.
- Existing mounts remain at, and are reused from, their original path after a location change.
- Compatible Acorn metadata changes are coalesced until a durability boundary.
- Successful mutations invalidate relevant kernel inode and entry caches.
- Mount identity, properties and Nautilus actions now follow detected format capabilities.
- The ADFS desktop MIME type now covers both hard-disc data and floppy images.
- Further Acorn File Forge integration is deferred until the remaining backlog
  is complete.
- Writable pair handling now checkpoints and maps the exact locked inodes,
  refuses hard-linked members and strips unrelated secrets from detached mount environments.
- Private state and mount directories now use no-follow descriptor-relative
  creation; desktop errors and diagnostics redact unrelated path data.
- Preferences, mount records, recovery manifests and repair audits now share
  one durable descriptor-relative atomic writer.

### Fixed

- Repair workflows now retain visible progress and completion details.
- Writable shutdown flushes dirty multi-handle buffers before final validation.
- Failed private-state and checkpoint copies preserve the last complete record
  and remove partial temporary data.

No public release has been cut yet. The first release will move these entries
under a dated version heading.
