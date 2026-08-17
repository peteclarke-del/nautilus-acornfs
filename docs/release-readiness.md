# Release readiness and support boundaries

This checklist supplements `TODO.md`; it does not turn an untested environment
claim into a completed item.

## Supported first-release boundary

- Ubuntu 24.04 LTS on amd64 only.
- GNOME/Nautilus 46 or later using the Nautilus 4 extension API.
- FUSE 3 with unprivileged mounts owned by the current user.
- Read-write only for validated paired BeebSCSI DAT/DSC old-map ADFS images.
- Read-only for ADFS S/M/L, DFS SSD/DSD and standard MMB images.
- Local regular image files. Network filesystems, removable/read-only media and
  unusual reflink behaviour remain outside the verified matrix.
- No claim yet for physical BeebSCSI hardware, ARM, extended MMB, ROMFS, UEF or
  Acorn File Forge hand-off.

## Candidate gate

- [ ] A project licence and Debian copyright metadata exist.
- [ ] Every required TODO acceptance item is checked with recorded evidence.
- [ ] `make check`, `make benchmark` and permitted live-FUSE tests pass on the
  tagged source.
- [ ] Wheel and source archive builds are reproducible and have an SBOM.
- [ ] The Ubuntu 24.04 amd64 package lifecycle smoke test passes.
- [ ] A clean GNOME session passes keyboard, screen-reader, light/dark, narrow
  window, 200 percent scaling and Nautilus file-operation workflows.
- [ ] Logout and host shutdown safely finalise dirty writable handles.
- [ ] A write-path candidate passes on real BeebSCSI hardware.
- [ ] Threat-model, fuzzing and dependency/licence scan findings contain no
  unresolved release blocker.
- [ ] Upgrade and rollback instructions match the candidate's state formats.
- [ ] Release archives, checksums, signatures and changelog are generated from
  the annotated tag.

## Evidence to retain

Record the commit and host version for manual desktop/hardware checks, CI URLs
for automated jobs, amd64 benchmark JSON, package contents, SBOM, checksums,
signature fingerprints and any accepted exception with its owner and review
date. Do not put private images or unrelated absolute paths in public evidence.
