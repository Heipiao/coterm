# Coterm CLI Publishing

This document describes the minimum release process for publishing `coterm` as an independent product on GitHub and PyPI.

## Scope

`coterm` is published as a standalone Python package under AGPL-3.0-or-later.

Current runtime assumptions:

- Python 3.10+
- Claude installed separately
- A reachable Coterm Hub
- Hub base URL can be provided by CLI flag, environment variable, saved config, or built-in default

## Before You Release

1. Update `coterm_cli.__version__`
2. Verify package metadata in `pyproject.toml`
3. Run packaging checks
4. Validate install and startup in a clean virtual environment
5. Draft release notes
6. Confirm the Hub story is accurate in docs:
   - default Hub is `http://127.0.0.1:18083`
   - `COTERM_HUB` / `COTERM_HUB_BASE_URL` override it
   - `coterm-hub` is a separate deliverable unless bundled deliberately

## Local Validation

From `coterm/cli`:

```bash
python -m build
python -m twine check dist/*
python -m venv .venv-release
source .venv-release/bin/activate
pip install dist/*.whl
coterm version
coterm doctor
```

If your local environment cannot reach PyPI during isolated builds, use:

```bash
python -m build --no-isolation
```

## GitHub Release

Recommended first channel:

1. Create a version tag, for example `cli-v0.1.0`
2. Build `sdist` and `wheel`
3. Upload both artifacts to GitHub Releases
4. Publish release notes with:
   - supported Python versions
   - current default Hub behavior
   - Claude prerequisite
   - known limitations
5. Upload `dist/*` as release artifacts

Repository:

- `https://github.com/Heipiao/coterm`
- workflow files must live under `coterm/.github/workflows/`

## PyPI Release

After GitHub release validation:

1. Ensure the package name is final
2. In PyPI, create a project or a pending publisher for `coterm-cli`
3. Configure Trusted Publishing for:
   - owner: `Heipiao`
   - repository: `coterm`
   - workflow: `cli-publish.yml`
   - environment: `pypi`
4. Publish via Trusted Publishing or Twine
3. Verify:

```bash
pip install coterm
coterm version
coterm doctor
```

## Product Notes

- `COTERM_HUB` and `COTERM_HUB_BASE_URL` override the Hub base URL
- If neither is set, the CLI falls back to `http://127.0.0.1:18083`
- `coterm hub start` should only be documented as supported when `coterm-hub` is independently installed or published
- GitHub Actions should verify packaging on every CLI change and publish only from tagged releases
