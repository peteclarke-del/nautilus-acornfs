# Architecture decisions

## Runtime and filesystem binding

The initial implementation uses Python 3.11 or later and pyfuse3 3.5 or later.
pyfuse3 is a maintained binding for libfuse 3 and supports async request
handling. Image code must remain independent of pyfuse3 so it can be tested
without mounting or elevated privileges.

Image selection resolves to a canonical source, Oaknut filesystem and geometry,
plus an explicit operation-capability profile. A complete DAT/DSC pair takes
precedence because its descriptor supplies hard-disc geometry that content
cannot recover. Otherwise Oaknut ranks content evidence; suffixes only break
equal-confidence ties. AcornFS currently accepts detected ADFS S/M/L floppy
geometry and rejects recognised-but-unsupported filesystems explicitly.
Read-only indexing uses Oaknut's core `Mount` protocol and feature-detects Acorn
metadata, filetype, size and free-space capabilities. Private old-ADFS access is
confined to the separately capability-gated BeebSCSI write and ranged-read paths.

The initial host baseline is Ubuntu 24.04 LTS or later, FUSE 3, and Nautilus 46
or later using the Nautilus 4 GObject-introspection API. CI also exercises
Python 3.11 through 3.14.

## Shared boundary with Acorn File Forge

Acorn File Forge already consumes `oaknut-disc==12.13.1` through
`oaknut.filesystem`. AcornFS will use the same public package API. Generic ADFS
parsing, geometry, catalogue and mutation logic belongs upstream in Oaknut;
AcornFS owns pairing, mount policy, caching, POSIX mapping and FUSE lifecycle.
No module will import code from the Acorn File Forge application package.

AcornFS pins Oaknut because complete allocation validation and efficient ranged
I/O currently require a small adapter over its old-ADFS internals. Those private
calls are isolated in `acornfs.core`; the Nautilus and FUSE layers never depend
on them directly.

## Writable transaction boundary

A writable session holds exclusive locks on both pair members and retains a
persistent pre-session checkpoint until clean validation and unmount. Each
individual mutation additionally captures compact before-images of the two map
sectors, affected directory blocks, and any existing live file allocation that
may be overwritten. Failure restores those sectors, runs complete integrity
validation, flushes them durably, and allows the session to continue only when
rollback is verified. Newly allocated sectors need no before-image because
restoring the map makes them free and unreachable.

Immediately after one logical mutation succeeds, AcornFS increments the 16-bit
old-map disc cycle ID modulo 65536 and asks Oaknut to regenerate both map
checksums. The ID update occurs inside the same sector transaction, after the
catalogue/data mutation and before durable flush, so a failed operation cannot
leave an observable cycle change. Multiple sector writes within one FUSE
operation do not produce multiple ID increments.

The same re-entrant lock covers on-disc mutation and the in-memory inode index
commit. This serialises writers while allowing ordinary reads from the stable
index. Large files bypass the whole-file LRU cache and read only the requested
sector range. Two adjacent reads on one FUSE handle establish a sequential
stream; subsequent reads fetch up to 256 KiB ahead. Speculative data is private
to that handle, discarded on a seek or writable access, and held under a 4 MiB
global LRU budget across the mount. FUSE growth is capacity-checked before its
memory buffer expands.

All writable handles for one inode share one userspace buffer. Writes and
truncation through any handle are immediately visible through every other
handle; any handle's `flush` or `fsync` commits the combined buffer as one atomic
image mutation. The buffer remains available until the final handle is released.
Compatible Acorn extended-attribute changes are similarly coalesced per inode,
remain immediately visible through the mounted view, and commit together at a
durability boundary or before a conflicting namespace mutation. A failed batch
remains pending so shutdown retains the recovery checkpoint.
When the FUSE loop stops normally or receives graceful `SIGINT`, the runner
commits every remaining dirty inode before detaching and before the image
performs final validation. A failed shutdown flush detaches the mount but makes
the image context retain its pre-mount recovery checkpoint.

Writable replies use zero entry and attribute timeouts, and successful content,
metadata and namespace mutations also send best-effort inode or directory-entry
invalidations to the kernel. Unsupported invalidation calls never turn an
already-successful on-image transaction into a reported failure.

Generated stress fixtures exercise a 64-level hierarchy at both sides of the
configured depth/node gates, the old-directory maximum of 47 entries, the
10-byte filename boundary, case collisions, and every non-POSIX display mapping.
Creation rejects any mapped value that cannot round trip through old ADFS; in
particular, NUL is an on-disc terminator rather than a valid creatable character.

## Package boundaries

- `acornfs.core`: untrusted-image parsing, validation and filesystem policy.
- `acornfs.fuse_adapter`: translation between pyfuse3 and core operations.
- `acornfs.cli`: commands that call the same application services as FUSE.
- `acornfs_nautilus`: thin Nautilus menu and properties integration.

The per-user desktop installer also registers narrow MIME types and a hidden URI
handler. DAT recognition uses old-format ADFS content magic rather than a generic
filename glob; ADFS floppy suffixes provide desktop discovery, while the core
still verifies content and geometry. DSC selection is extension-based but must
pass pair and descriptor validation. MIME, double-click and `acornfs:` URI opens
all converge on the same read-only desktop-mount path.

The Nautilus extension communicates with the CLI, which runs desktop mounts as
collected transient systemd user services when available. It must not hold
writable images open or implement filesystem parsing itself.

Desktop mount roots are resolved by one preferences boundary. The default
sidebar mode remains below the user's home directory; runtime mode resolves per
session below `$XDG_RUNTIME_DIR/acornfs/images`, and custom mode requires an
absolute path. Preferences are atomically replaced in a private XDG config
directory. `ACORNFS_MOUNT_ROOT` is a process-scoped override. Privacy-safe
diagnostics report only the selected mode and source, never the resolved path.

Image creation follows the same boundary. The extension launches a desktop CLI
action, while `acornfs.core` asks Oaknut to create the filesystem under unique
temporary names in the destination directory. AcornFS then performs complete
structural validation and uses create-only hard links to publish the DAT and DSC
without an overwrite window. A failure publishing either member removes any
published member and all temporary files.

## Mount identity and shutdown

`/proc/self/mountinfo` is authoritative for active FUSE mounts. A private
per-user runtime record enriches each entry with the canonical primary image and
optional companion paths, their device/inode identities, the daemon PID and
access mode. Records use a `0700`
directory and `0600` files and are removed only after the image context has
completed close-time flush and validation. Dead records are pruned; a live
post-detach record represents a writable daemon still finalising.

The daemon publishes its private identity immediately before FUSE
initialisation. The kernel mount remains authoritative, so this record cannot
advertise a mount prematurely; publishing first avoids a race where the kernel
mount is visible while libfuse is still returning from initialisation. Desktop
launchers can therefore recognise readiness without timing out and killing a
healthy writable daemon.

Read-only desktop mounts may detach lazily. Writable mounts may not: callers
wait for the lifecycle record to disappear and then verify that no recovery
checkpoint remains before claiming a safe unmount. Diagnostics deliberately
reduce paths to basenames and hash device/inode identities; they never inspect
or copy image content.

## Repair boundary

Validation findings are converted into typed, deterministic repair-plan actions
without opening either pair member writable. The planner groups related findings,
records risk, distinguishes future automatic candidates from mandatory human
decisions, and exposes equivalent human and JSON representations. Mutation is a
separate boundary: only complete low-risk catalogue-normalisation plans and
exact-ADFS-boundary reserved-tail padding have an apply entry point. It requires
exact-filename confirmation, exclusive pair locks, a pre-repair checkpoint, full
verification and a retained audit record in one operation; none of those
guarantees is inferred from planning. All other plans remain read-only guidance.
The repair API reports monotonic stage progress, including byte-level checkpoint
copying, to desktop callers without coupling the core transaction to Zenity. The
desktop progress window is deliberately non-cancellable after typed confirmation;
the transaction remains the sole authority for rollback and checkpoint retention.

Long read-only validation uses cooperative cancellation points between directory
and extent checks. Recovery stages complete DAT and DSC replacements alongside
their targets and may be cancelled during those copies without modifying either
target. The final replacement sequence has no cancellation point: it is a short
commit boundary, after which the checkpoint is removed only when both replacements
and their parent-directory synchronisation have completed.
