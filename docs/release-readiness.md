# Release readiness and support boundaries

This checklist supplements `TODO.md`; it does not turn an untested environment
claim into a completed item.

## Supported first-release boundary

- Ubuntu 24.04 LTS on amd64 only.
- GNOME/Nautilus 46 or later using the Nautilus 4 extension API.
- FUSE 3 with unprivileged mounts owned by the current user.
- Read-write for validated BeebSCSI DAT/DSC, standalone ADFS
  S/M/L/D/E/E+/F/F+/G/G+, FileCore/unpaired raw ADFS hard-disc, DFS SSD/DSD and
  standard/extended MMB images.
- ROMFS remains read-only. MMB writes are confined to existing slots marked
  read-write; whole-slot catalogue operations remain outside the release scope.
- Local regular image files. Unit coverage proves read-only backing storage
  remains browsable, writable opening fails closed, and checkpoint copying
  falls back when reflinks are unavailable. Real network and removable
  filesystems remain outside the verified matrix.
- No claim yet for physical BeebSCSI hardware, ARM or UEF.

## Candidate gate

- [x] A project licence and Debian copyright metadata exist.
- [ ] Every required TODO acceptance item is checked with recorded evidence.
- [ ] `make check`, `make benchmark` and permitted live-FUSE tests pass on the
  tagged source.
- [x] Privileged Ubuntu amd64 CI mounts either DAT/DSC member read-only with
  terminal tools and preserves retained data plus Acorn metadata across the
  full writable mutation, validation and remount lifecycle.
- [x] In-process FUSE and core tests cover writable ADFS Old/New/Big directory,
  FileCore, DFS SSD/DSD and MMB file lifecycles with rollback and recovery.
- [x] Wheel and source archive builds are reproducible and have an SBOM.
- [ ] The Ubuntu 24.04 amd64 package lifecycle smoke test passes.
- [ ] A clean GNOME session passes keyboard, screen-reader, light/dark, narrow
  window, 200 percent scaling and Nautilus file-operation workflows using the
  [desktop acceptance matrix](desktop-acceptance.md).
- [ ] Logout and host shutdown safely finalise dirty writable handles.
- [ ] A write-path candidate passes on real BeebSCSI hardware.
- [ ] Threat-model, fuzzing and dependency/licence scan findings contain no
  unresolved release blocker.
- [ ] Upgrade and rollback instructions match the candidate's state formats.
- [ ] Release archives, checksums, signatures and changelog are generated from
  the annotated tag; archives, SBOM and unsigned checksums are automated, while
  signing awaits an approved key policy.

## Evidence to retain

Record the commit and host version for manual desktop/hardware checks, CI URLs
for automated jobs, amd64 benchmark JSON, package contents, SBOM, checksums,
signature fingerprints and any accepted exception with its owner and review
date. Do not put private images or unrelated absolute paths in public evidence.
