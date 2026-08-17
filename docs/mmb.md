# MMB containers

AcornFS mounts standard BBC Micro MMB containers read-only. The supported
layout is one 8 KiB catalogue followed by 511 fixed 200 KiB SSD payloads. Only
slots whose catalogue status is locked or read-write are presented; unformatted
and invalid slots remain hidden. The format definition follows the documented
[MMB/SSD utility layout](https://sweh.spuddy.org/Beeb/mmb_utils.html#disk-format).

## Mounted namespace

Each formatted slot is a root directory named with a zero-padded slot number
and its MMB catalogue label, for example:

```text
000 - WELCOME/
042 - UTILITIES/
```

The numeric prefix is authoritative and prevents duplicate or stale labels from
colliding. Within each slot, Oaknut identifies and mounts the SSD catalogue.
The existing DFS mapping then presents `$` and populated `A`-`Z` catalogue
prefixes as directories. MMB labels are presentation metadata and need not
match the DFS title stored inside the corresponding SSD.

AcornFS reads the 8 KiB catalogue eagerly but opens slot payloads lazily. A
least-recently-used cache retains at most eight Oaknut DFS mounts (about 1.6 MiB
of payload buffers) regardless of the number of formatted slots. The filesystem
index remains bounded by the global AcornFS node limit.

Extended MMB containers are rejected with an explicit message. Their repeated
catalogue/payload extent layout must be covered independently before support is
enabled; silently treating the first extent as a complete container would hide
slots and misreport capacity.

## Future mutation contract

MMB mounts and all contained SSDs are currently read-only, including slots
whose MMB status says read-write. That status describes the BBC-side access
policy; it does not grant Linux write access.

Future slot insertion, replacement, ejection or access-mode changes must follow
one container-level transaction model:

1. Take an exclusive lock on the complete MMB and refuse changes while any slot
   from that container is mounted.
2. Recheck the container identity, length, catalogue and target slot after the
   lock is held.
3. Validate an incoming image as an exact 200 KiB DFS SSD before mutation.
4. Create a durable checkpoint containing the 8 KiB catalogue and complete
   target payload. A reflink of the whole container may be used when available.
5. For insertion or replacement, write and flush the payload first, then commit
   the 16-byte catalogue entry and flush the container. The catalogue status is
   the visibility/commit boundary.
6. Ejection marks the catalogue entry unformatted without erasing payload bytes;
   restoration therefore remains possible until a later insertion commits.
7. Refuse replacement of a formatted slot unless the user explicitly requested
   replacement. Refuse ejection of a configured boot slot unless the same
   transaction also selects a valid replacement boot slot.
8. Re-read the target slot through Oaknut and verify the catalogue after commit.
   On any failure, restore the checkpoint before releasing the lock.

Writable access inside a mounted slot is a separate, later feature. It requires
the same whole-container lock and checkpoint guarantees; a slot's nominal
read-write status alone is insufficient.
