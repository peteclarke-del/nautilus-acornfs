# Acorn metadata mapping

AcornFS keeps the on-disc ADFS metadata authoritative. The POSIX view is an
interoperability layer and does not invent sidecar files.

| Acorn value | Linux presentation | Round trip |
|---|---|---|
| Load address | `user.acorn.load`, eight uppercase hexadecimal digits | Lossless |
| Execute address | `user.acorn.execute`, eight uppercase hexadecimal digits | Lossless |
| RISC OS filetype | `user.acorn.filetype`, three uppercase hexadecimal digits when encoded in a timestamped load address | Lossless when present |
| Locked bit | `user.acorn.locked`, `0` or `1`; also removes POSIX write bits from the presentation | Lossless |
| ADFS pathname | `user.acorn.path` | Informational, read-only |
| Source filesystem | `user.acorn.source`, currently `adfs` | Informational, read-only |

ADFS has no general POSIX owner, group, permission-bit, nanosecond timestamp,
symbolic-link, device-node or Unix execute-bit equivalents. AcornFS therefore
uses the mounting user's identity, conservative synthetic mode bits and the DAT
timestamp where a FUSE timestamp is required. Changing ordinary POSIX ownership,
mode or timestamps is unsupported; applications that need exact Acorn metadata
should use the extended attributes.

Filename display maps ADFS `/` to `∕`, control characters to Unicode control
pictures, and the special names `.` and `..` to full-width forms. New names must
be representable as 7-bit ASCII, contain at most 10 bytes, and exclude `.`, `:`,
NUL and carriage return. AcornFS never silently sanitises a new name. NUL is
rejected because old ADFS readers treat it as a name terminator, which would
otherwise make a newly created name reopen in truncated form.

ADFS lookup is case-insensitive while the stored spelling remains visible.
AcornFS therefore resolves Linux lookups case-insensitively and refuses creation
of a file or directory whose name differs from an existing sibling only by case.
It does not invent a case-sensitive overlay that the on-disc catalogue cannot
round trip.

Optional `.inf` export sidecars are hidden: AcornFS neither generates nor
exposes them in mounted images. Extended attributes are the authoritative
lossless metadata interface. A future explicit export tool may generate
sidecars, but mounting will not create them implicitly.
