# Versioning and release policy

Nautilus AcornFS uses Semantic Versioning. The version in `pyproject.toml` is
the single source of truth and release tags use the matching `vMAJOR.MINOR.PATCH`
form. While the project remains below 1.0, a minor version may intentionally
change command or integration interfaces; patch versions remain compatible and
are reserved for fixes and safe internal improvements.

Every user-visible pull request updates the `Unreleased` section of
`CHANGELOG.md`. A release moves those entries beneath a dated version heading,
leaves a fresh `Unreleased` section, and updates `pyproject.toml` in the same
reviewed commit. Versions are never inferred from the Git branch or rewritten
during package builds.

## Release procedure

1. Confirm the intended release acceptance items in `TODO.md` are complete;
   environment, Nautilus, FUSE and hardware checks must not be replaced by unit
   tests or assumptions.
2. Run `make check`, `make benchmark`, `make test-live` on a permitted amd64
   FUSE host, install `.[release]`, and run `make release`.
3. Run `make package-smoke` and `make deb`. Inspect the Debian manifest, install
   the `.deb` into a clean Ubuntu 24.04 amd64 environment, exercise the command
   and Files loader, remove it, then run the documented mount, writable-edit
   and recovery acceptance tests on a GNOME host.
4. Set the version, date the changelog section, and review the full diff.
5. Merge the release commit, create the matching annotated tag, then publish
   release notes derived from the changelog.
6. Verify `build/release/SHA256SUMS`, inspect the validated CycloneDX SBOM, sign
   the source archive and checksum manifest under the approved release-key
   policy, and attach only those artefacts built from the tag. The `.deb` and
   add-on ZIP are user-installable convenience artefacts and must come from the
   same reviewed tag.
7. Keep the previous release and recovery documentation available so users can
   restore images and checkpoints before attempting an incompatible upgrade.

The project is distributed under the MIT licence. Release signing remains a
separate acceptance item until a release-key policy exists. The automated
release job builds the wheel, source archive, add-on and amd64 Debian package
with one commit-derived timestamp, verifies reproducibility, emits a
CycloneDX 1.6 SBOM and writes an unsigned checksum manifest covering every
artifact. The Debian build accepts only the audited hash-pinned Oaknut runtime
and its MIT-licensed helpers. Automated lifecycle tests cover system-package
install and removal plus managed add-on installation, atomic upgrade and
uninstall. Do not mark these gates complete without the required evidence.

## Compatibility and support

The first release line supports amd64 on the Ubuntu, GNOME and Nautilus versions
documented in the README. BeebSCSI DAT/DSC, standalone ADFS
S/M/L/D/E/E+/F/F+/G/G+, FileCore/unpaired raw ADFS hard discs, DFS SSD/DSD and
standard/extended MMB images support protected writable mounts. Complete
standard Acorn HFE v1/HFEv3 containers have the same protection when the
Greaseweazle host tools are installed. ROMFS remains
read-only. Other architectures, image formats and physical-hardware claims
remain unsupported until their specific TODO and test-matrix entries close.

The on-disc safety boundary has priority over host API compatibility. A release
must refuse uncertain writes rather than weaken validation to preserve an old
command outcome. Any unavoidable command, metadata or recovery-format change is
identified in the changelog with migration or rollback instructions.
Oaknut upgrades follow the dedicated exact-family and private-adapter gate in
[oaknut-compatibility.md](oaknut-compatibility.md); mixed or floating Oaknut
families are outside the supported boundary.

The support boundary, candidate gate and evidence requirements are in
[release-readiness.md](release-readiness.md). User and administrator procedures
are maintained separately in [user-guide.md](user-guide.md) and
[admin-guide.md](admin-guide.md).
