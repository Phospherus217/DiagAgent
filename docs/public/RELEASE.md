# Public release boundary

The release builder copies only the engineering paths listed in `release_manifest.json`. It does not recursively discover workspace files. The manifest deliberately excludes portfolio archives, papers, human-study material and private reports.

```powershell
python scripts/build_public_release.py --output .validation/public_release
```

The generated `PUBLIC_CHECKSUMS.json` records copied file hashes. The standalone verifier installs that curated copy from an unrelated working directory and runs the offline test, mock CLI and recovery CLI checks. Real GIMP and model-marked tests are outside this offline gate. This command prepares local files only; it does not publish to GitHub.

```powershell
python scripts/verify_standalone.py --python path/to/isolated/python --output .validation/standalone_check
```

Supply an isolated interpreter with the project's dependencies, pytest, setuptools and wheel already installed. Installation uses `--no-deps --no-build-isolation`; validation itself does not download dependencies or call models.

The public file list includes source, benchmark tasks/assets, the newly exported mock sample, public documentation, selected scripts and tests. It excludes `tests/test_portfolio_package.py` (two archival integration tests) and `tests/test_portfolio_publication.py` (eight tests for excluded archival publication tooling). Both remain unchanged in the local workspace and are included in the full local regression command. Runtime, evaluator, protocol and all public recovery tests remain in the export.

`src/diagagent/` is the existing source location. The limited changes there extend evidence views and recovery audit persistence; they do not relocate the runtime or change task/evaluator contracts. Sample paths are made portable; original local runs and archived evidence are not changed. Publish the curated export, not the full research workspace or its Git history.
