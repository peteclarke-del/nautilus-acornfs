# Acorn metadata mapping

AcornFS keeps the on-disc ADFS metadata authoritative. The POSIX view is an
interoperability layer and does not invent sidecar files.

| Acorn value | Linux presentation | Round trip |
|---|---|---|
| Load address | `user.acorn.load`, eight uppercase hexadecimal digits | Lossless |
| Execute address | `user.acorn.execute`, eight uppercase hexadecimal digits | Lossless |
| RISC OS filetype | `user.acorn.filetype`, three uppercase hexadecimal digits when encoded in a timestamped load address | Lossless when present |
| Locked bit | `user.acorn.locked`, `0` or `1`; also removes POSIX write bits from the presentation | Lossless |
| ROMFS run-only bit | `user.acorn.run_only`, `0` or `1` | Lossless, informational on a read-only mount |
| ADFS pathname | `user.acorn.path` | Informational, read-only |
| Source filesystem | `user.acorn.source`: `adfs`, `acorn-dfs`, `watford-dfs` or `acorn-romfs` | Informational, read-only |

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

ROMFS is different: its flat catalogue permits distinct names such as `Case`
and `case`, so Linux lookup remains case-sensitive. The same non-POSIX display
mapping applies, including `/` to `∕`, but ROMFS mounts do not accept renamed or
new entries.

Optional `.inf` sidecars remain hidden from mounted images; mounting never
creates them implicitly. The explicit `export-file` command creates a host file
and matching traditional `.inf` record containing the full Acorn path, load and
execution words, byte length and lock state. Publication is create-only with
complete rollback, so neither destination is overwritten or left half-published
when the command returns.

`import-file` accepts the same Acorn File Forge/Oaknut-compatible record,
including quoted paths and `L`/`Locked` markers. It validates a recorded length
before opening the image writable, then creates the data and metadata in one
rollback-protected mutation. Without a sidecar it recognises Oaknut's supported
`,xxx`, `,load,exec` and `,load-exec` filename encodings, then falls back to
neutral zero addresses. Extended attributes remain the authoritative metadata
interface inside a live mount.
