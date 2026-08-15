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

## Package boundaries

- `acornfs.core`: untrusted-image parsing, validation and filesystem policy.
- `acornfs.fuse_adapter`: translation between pyfuse3 and core operations.
- `acornfs.cli`: commands that call the same application services as FUSE.
- `acornfs_nautilus`: thin Nautilus menu and properties integration.

The Nautilus extension communicates with the CLI, which runs desktop mounts as
collected transient systemd user services when available. It must not hold
writable images open or implement filesystem parsing itself.
