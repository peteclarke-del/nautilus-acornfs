# Nautilus AcornFS

Nautilus AcornFS is an in-progress, user-space filesystem for Acorn disk
images. The first supported format will be paired BeebSCSI `DAT` and `DSC`
files. Images will be mounted through FUSE 3 and exposed to Nautilus through a
small extension, while the filesystem engine remains usable from any Linux
application.

The project is deliberately read-only while the image parser and validation
rules mature. See [TODO.md](TODO.md) for the roadmap.

## Current functionality

- Discover a matching BeebSCSI `DAT`/`DSC` pair from either member.
- Reject missing or ambiguous pairs.
- Parse and validate the geometry in a 22-byte BeebSCSI descriptor.
- Report pair metadata through `acornfs inspect`.

## Development

Nautilus AcornFS requires Python 3.11 or later. To create a development
environment:

```shell
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
```

FUSE development also needs the operating system's FUSE 3 development package:

```shell
sudo apt install fuse3 libfuse3-dev pkg-config
python -m pip install -e '.[dev,fuse]'
```

No mount operation is implemented yet.

