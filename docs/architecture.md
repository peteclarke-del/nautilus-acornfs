# Architecture decisions

## Runtime and filesystem binding

The initial implementation uses Python 3.11 or later and pyfuse3 3.5 or later.
pyfuse3 is a maintained binding for libfuse 3 and supports async request
handling. Image code must remain independent of pyfuse3 so it can be tested
without mounting or elevated privileges.

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
sector range; FUSE growth is capacity-checked before its memory buffer expands.

## Package boundaries

- `acornfs.core`: untrusted-image parsing, validation and filesystem policy.
- `acornfs.fuse_adapter`: translation between pyfuse3 and core operations.
- `acornfs.cli`: commands that call the same application services as FUSE.
- `acornfs_nautilus`: thin Nautilus menu and properties integration.

The Nautilus extension communicates with the CLI, which runs desktop mounts as
collected transient systemd user services when available. It must not hold
writable images open or implement filesystem parsing itself.
