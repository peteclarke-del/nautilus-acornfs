# Security policy

## Supported versions

No public release has been made yet. Security fixes are currently applied only
to the latest `main` branch. The first supported release line will be documented
here when it is published; older development snapshots receive no security
updates.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do not
open a public issue for a vulnerability that could corrupt images, escape mount
boundaries, disclose local data, or execute commands. Include the affected
commit/version, Ubuntu and Nautilus versions, image format, read-only/read-write
mode, minimal reproduction and privacy-safe diagnostics where possible.

Do not attach proprietary disk images, recovery checkpoints, credentials,
unredacted journals or unrelated paths. A generated `acornfs diagnostics
--json` report is designed for review and sharing without image contents.

The maintainer should acknowledge a complete report, reproduce it against a
generated fixture where possible, assess image-confidentiality/integrity and
host impact, prepare tests and a coordinated fix, and publish an advisory after
users have a safe upgrade path. Timing depends on severity and the need to
validate write-path changes against real hardware.

## Security boundary

Acorn images are untrusted input. Read-only is the default; uncertain writable
geometry is refused. Mounts use `nodev`, `nosuid` and `noexec`, remain owned by
the current user, and do not require root. Writable operations use exclusive
locks, pre-write checkpoints, transaction rollback, external-change detection
and post-write validation.

The supported boundary does not include hostile multi-user access to another
user's mount, remote image URIs, arbitrary desktop IPC endpoints, or running
AcornFS/Nautilus as root. These are not valid workarounds for a refused image or
host configuration.

The reviewed assets, attacker inputs, mitigations and remaining release gates
are documented in [docs/threat-model.md](docs/threat-model.md).
