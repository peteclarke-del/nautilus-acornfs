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
be representable as 7-bit ASCII and obey the ADFS directory-entry limit. AcornFS
never silently sanitises a new name. Optional `.inf` export sidecars are not
currently generated or exposed.
