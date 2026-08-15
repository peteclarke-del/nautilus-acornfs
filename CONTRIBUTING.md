# Contributing

Changes should be small, tested and safe for untrusted disk images. Do not add
fixtures copied from private or copyrighted media; generate the smallest bytes
needed by the test instead.

Before opening a pull request, run:

```shell
make check
```

Code must pass Ruff formatting and linting, mypy, and pytest. New parser paths
need tests for malformed, truncated and boundary-value input. Filesystem writes
must not be added without an explicit recovery design and failure-injection
tests.

The project licence has not yet been selected. Contributions cannot be accepted
until that repository decision has been recorded.

