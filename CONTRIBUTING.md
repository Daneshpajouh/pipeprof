# Contributing

```bash
make dev        # .venv with the dev extras
make check      # ruff check, ruff format --check, mypy --strict, pytest with coverage
bats tests/shell && shellcheck scripts/*.sh
```

- Code: PEP 8 via `ruff format` (line length 99), type hints checked with
  `mypy --strict`.
- Tests: a change to a parser needs a file in `tests/data` that shows the
  case, described in `tests/data/README.md`. Mark hand-written files as such.
- Numbers in the docs come from runs; put the command next to the number.
- Commits: imperative subject under 72 characters.
- Releases: bump `version` in `pyproject.toml` and `__init__.py`, add a
  CHANGELOG entry, tag `vX.Y.Z`; `.github/workflows/release.yml` builds,
  publishes to TestPyPI then PyPI with trusted publishing, and creates the
  GitHub release.
