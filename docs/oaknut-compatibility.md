# Oaknut compatibility policy

AcornFS uses Oaknut's public filesystem interfaces wherever they provide the
required capability. Old-map ADFS validation, ranged reads and rollback-safe
mutation additionally need a small private adapter contained entirely within
`acornfs.core`. No FUSE, Nautilus or desktop module may add a direct dependency
on an Oaknut private attribute.

All Oaknut distributions must stay on one exact release. The supported set and
version are declared together in `pyproject.toml`; partial or floating upgrades
are not accepted. The current supported family is Oaknut 12.15.1, matching
Acorn File Forge's filesystem dependencies.

## Upgrade gate

An Oaknut upgrade uses a dedicated pull request and must:

1. update every Oaknut pin together and describe upstream behavioural changes;
2. update the explicit private-adapter contract test rather than weakening or
   deleting it when an API moves;
3. run the complete Python matrix, amd64 live-FUSE lifecycle, generated image
   fixtures, mutation failure injection, validation, repair and recovery tests;
4. pass coverage-guided parser smoke tests, amd64 performance budgets,
   dependency auditing and the clean install/upgrade/uninstall package test;
5. retain the previous version when any write-path or validation difference is
   not understood, with uncertain images continuing to fail closed; and
6. record any user-visible, on-disc or state-format consequence in the
   changelog and migration documentation.

The contract test inventories the private surface currently relied upon: the
old-ADFS object, free-space map and its checksum buffer, directory format and
read helpers, path resolution, raw ranged sector access and catalogue entry
disc addresses. Ordinary public protocol behaviour remains covered by the
format, image, validation and FUSE test suites.

Private API compatibility is a source-level support promise only for the exact
pinned family. AcornFS does not claim compatibility with arbitrary newer Oaknut
releases, and packaging must never resolve a mixed family dynamically.
