# MMB containers

AcornFS mounts standard and extended BBC Micro MMB containers read-only or
read-write. A
standard extent is one 8 KiB catalogue followed by 511 fixed 200 KiB SSD
payloads. An extended container concatenates 2 to 16 extents, with byte 8
of the first header declaring the number of additional extents. Only slots whose
catalogue status is locked or read-write are presented; unformatted and invalid
slots remain hidden. The format definition follows the documented
[MMB/SSD utility layout](https://sweh.spuddy.org/Beeb/mmb_utils.html#disk-format).

## Mounted namespace

Each formatted slot is a root directory named with a zero-padded global slot
number and its MMB catalogue label, for example:

```text
000 - WELCOME/
042 - UTILITIES/
0511 - EXTENDED/
```

The numeric prefix defines slot identity and prevents duplicate or stale labels
from colliding. Within each slot, Oaknut identifies and mounts the SSD catalogue.
The existing DFS mapping then presents `$` and populated `A`-`Z` catalogue
prefixes as directories. MMB labels are presentation metadata and need not
match the DFS title stored inside the corresponding SSD.

Standard containers use three digits; extended containers use four so lexical
and numeric order remain identical across extent boundaries. AcornFS validates
the exact declared length and every repeated 8 KiB catalogue. It also requires
recognisable DFS evidence in every populated extent, preventing a valid first
extent from disguising arbitrary trailing data. Global boot slots may refer to
any slot within the declared extent count.

Slot DFS mounts are created one at a time during bounded namespace indexing
and later access. A least-recently-used cache retains at most eight Oaknut DFS
mounts (about 1.6 MiB of payload buffers) regardless of the number of formatted
slots. The filesystem index remains bounded by the global AcornFS node limit.
Containers that are truncated, oversized, declare an out-of-range boot slot or
contain an unknown status in any extent fail closed.

## Writable behaviour

An MMB writable mount holds an exclusive lock on the whole container, validates
every formatted slot and creates a persistent whole-container checkpoint before
accepting changes. Each logical mutation has another private whole-container
before-image. AcornFS uses a reflink when the state directory shares a compatible
filesystem with the image, otherwise it makes a bounded durable copy. Failed
operations are restored and validated before the mounted session may continue.

Files and Acorn metadata may be created, replaced, renamed and removed inside a
slot whose MMB status is read-write. Locked slots remain readable but reject all
mutations. A file cannot be renamed between slots. The virtual slot and DFS
prefix directories cannot be created, removed or moved because they do not
represent directories in the contained SSD.

## Deferred slot catalogue operations

Insertion, replacement, ejection and access-mode changes are not yet exposed.
Those operations must follow this container-level transaction model:

1. Take an exclusive lock on the MMB and refuse changes while any slot
   from that container is mounted.
2. Recheck the container identity, length, catalogue and target slot after the
   lock is held.
3. Validate an incoming image as an exact 200 KiB DFS SSD before mutation.
4. Create a durable checkpoint containing the 8 KiB catalogue and full
   target payload. A reflink of the whole container may be used when available.
5. For insertion or replacement, write and flush the payload first, then commit
   the 16-byte catalogue entry and flush the container. The catalogue status is
   the visibility/commit boundary.
6. Ejection marks the catalogue entry unformatted without erasing payload bytes;
   restoration therefore remains possible until a later insertion commits.
7. Refuse replacement of a formatted slot unless the user requested
   replacement. Refuse ejection of a configured boot slot unless the same
   transaction also selects a valid replacement boot slot.
8. Re-read the target slot through Oaknut and verify the catalogue after commit.
   On any failure, restore the checkpoint before releasing the lock.

The mounted write path already supplies the same whole-container lock,
checkpoint, rollback and validation guarantees for file operations inside an
existing read-write slot. It does not modify the MMB catalogue.
