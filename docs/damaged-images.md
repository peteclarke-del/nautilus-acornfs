# Damaged images and recovery

AcornFS treats every image as untrusted and never repairs one merely because it
was inspected or mounted. Preserve the original DAT and DSC together before any
recovery work. Do not substitute a descriptor from another image simply because
its filename matches: cylinder and head geometry determines how the DAT is
addressed.

## Assessment workflow

1. Copy both original files to read-only archival storage.
2. Run `acornfs validate IMAGE` on a working copy.
3. Save `acornfs validate --json IMAGE` with the working copy.
4. Run `acornfs repair-plan IMAGE` to group findings into possible operations.
5. If the complete plan is marked applicable, run `acornfs repair IMAGE --confirm
   DAT_FILENAME` only on the working copy.
6. Mount read-only only when AcornFS can safely traverse the complete directory
   tree. Copy important files out before considering any future repair.

A fatal allocation finding blocks read-write mounting but does not necessarily
block read-only traversal. Invalid map checksums, unreadable directories,
truncation and out-of-range structures are rejected when safe traversal cannot
be established. AcornFS reports classified findings rather than attempting to
guess missing sectors or ownership.

JSON validation reports include a top-level `schema_version` and a versioned
`compatibility_profile`. The initial profile is
`beebscsi-adfs-old-map` version 1. Consumers must use those machine fields rather
than parsing translated messages; finding codes remain stable within the report
schema.

## Repair-plan interpretation

The planner labels actions as automatic candidates or manual decisions and
assigns a risk. A candidate is a design statement, not permission to modify the
image. In particular, rebuilding a free-space map is high risk because an
incorrect catalogue can make allocated data appear free. Geometry mismatch,
unreadable structures and conflicting allocated extents always require a human
decision or restoration from a known-good copy.

Automatic application is limited to normalising unusual directory entry
lengths, clearing non-zero start sectors from zero-length files, and restoring a
zero-filled DAT tail when the file ends exactly at the validated ADFS boundary
below its DSC capacity. The command requires the exact DAT filename as
confirmation and refuses the entire plan if any other action is present. It
locks both files, writes an audit before opening the image writable, creates a
recovery checkpoint before the first mutation, verifies the complete image
afterward, and retains the JSON audit in the AcornFS state directory. A failed
operation retains its checkpoint for `acornfs recover`.

Free-space-map reconstruction remains a high-risk planning aid only. It is never
applied automatically, even when labelled a reconstruction candidate.

## Interrupted writable sessions

An interrupted writable mount is different from pre-existing image damage. If
`acornfs recover IMAGE` reports a checkpoint, either restore that checkpoint or
explicitly accept the current image with `--discard` after independent
validation. Never delete recovery state manually; the recovery command locks
both pair members and completes the operation durably.
