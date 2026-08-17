# Performance baseline

The first release supports amd64 only. Its repeatable baseline uses a generated
20 MB BeebSCSI image, a 32-entry root, a 16-level path, sixteen cached 1 KiB
files and one 4 MiB file read in 64 KiB ranges. Fixture creation is excluded
from every measurement. The open measurement includes DSC discovery, ADFS
mounting and eager directory indexing; other measurements use that open image.
The workload does not purge the host page cache, so it measures repeatable
application/index costs and warm read paths rather than claiming cold-disk I/O.
The memory stress additionally allocates the exact immutable node and index
container types for the supported 100,000-node ceiling together with one 8 MiB
open-write buffer.

Run the same workload locally with:

```shell
make benchmark
```

The command writes `build/performance/amd64.json` and fails if a budget is
missed. CI uploads that report as the `performance-amd64` artefact on every
pull request and main-branch build. Reports include the source revision,
platform, complete fixture definition, sample summaries and each applied
budget, making changes comparable without parsing console output.

## First-RC amd64 budgets

| Workload | Budget |
| --- | ---: |
| Open and eagerly index the image | p95 at most 1,000 ms |
| List the 32-entry root from the index | p95 at most 250 µs |
| Resolve the 16-level path | p95 at most 500 µs |
| Read a warm cached 1 KiB file | p95 at most 100 µs |
| Read the 4 MiB file through ranged reads | median at least 15 MiB/s |
| Python allocation peak while opening | at most 32 MiB |
| Python peak for 100,000 indexed nodes plus one 8 MiB write buffer | at most 64 MiB |

These are regression guardrails for shared GitHub amd64 runners, not claims
about all host storage. They are intentionally conservative for the first RC
and should be tightened from retained CI history. Raspberry Pi 4/5 measurements
and live-FUSE latency remain separate work rather than being inferred from amd64
results.
