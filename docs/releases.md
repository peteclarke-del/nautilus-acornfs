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
3. Run `make package-smoke`, then install the built wheel into a clean supported
   Ubuntu environment and run the documented mount, writable-edit and recovery
   acceptance tests.
4. Set the version, date the changelog section, and review the complete diff.
5. Merge the release commit, create the matching annotated tag, then publish
   release notes derived from the changelog.
6. Verify `build/release/SHA256SUMS`, inspect the validated CycloneDX SBOM, sign
   the source archive and checksum manifest under the approved release-key
   policy, and attach only those artefacts built from the tag.
7. Keep the previous release and recovery documentation available so users can
   restore images and checkpoints before attempting an incompatible upgrade.

The project must have an explicit licence before its first public release.
Signed archives and Debian artefact production remain separate acceptance items
until a licence and release-key policy exist. The automated release job builds
the wheel and source archive twice with one commit-derived timestamp, compares
their SHA-256 digests, emits a reproducible CycloneDX 1.6 SBOM and writes an
unsigned checksum manifest. The automated wheel lifecycle test covers managed
installation, atomic upgrade and uninstall while preserving preferences,
checkpoint-shaped state and repair audits.
No release procedure may claim those guarantees early.

## Compatibility and support

The first release line supports amd64 on the Ubuntu, GNOME and Nautilus versions
documented in the README. BeebSCSI DAT/DSC old-map ADFS is the only writable
image format; ADFS S/M/L floppies are supported read-only. Other architectures,
image formats and physical-hardware claims remain unsupported until their
specific TODO and test-matrix entries close.

The on-disc safety boundary has priority over host API compatibility. A release
must refuse uncertain writes rather than weaken validation to preserve an old
command outcome. Any unavoidable command, metadata or recovery-format change is
called out explicitly in the changelog with migration or rollback instructions.
Oaknut upgrades follow the dedicated exact-family and private-adapter gate in
[oaknut-compatibility.md](oaknut-compatibility.md); mixed or floating Oaknut
families are outside the supported boundary.

The complete support boundary, candidate gate and evidence requirements are in
[release-readiness.md](release-readiness.md). User and administrator procedures
are maintained separately in [user-guide.md](user-guide.md) and
[admin-guide.md](admin-guide.md).
