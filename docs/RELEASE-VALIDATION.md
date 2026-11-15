# Release validation — 2026-11-15

## Source

Preserved original: FaultCore-M5-closed.zip.
SHA-256: 51681051ac2f1905f4e0149ab0c648098eaa236e65cf780aa525075b07212efd.

## Local checks actually executed

Host: Darwin arm64, Python 3.9.6.
Command: `python3 -m pytest -q tests/test_branding.py tests/test_budget_and_final.py tests/test_atomic_write.py -k 'not cli'`.
Result: **15 passed, 2 deselected**. The selection includes all four branding tests; deselected tests invoke the fixture CLI. Budget tests use mocked execution. File publication tests operate on temporary files.

`python3 -m faultcore --banner` displays the actual ASCII identity. Help, version, interactive banner suppression and clean JSON output were checked by the branding tests.

## Build limitation

The available host Python is older than the declared Python >=3.10 requirement; its setuptools is 58.0.4, below the declared build requirement >=61. An offline no-isolation wheel attempt generated incorrect UNKNOWN metadata; that unusable wheel was discarded and is not delivered. No dependencies were downloaded or installed globally. Installation and uninstallation of this modified release have not been verified locally. Use Python >=3.10 and setuptools >=61 for build/install validation.

## Upstream evidence boundary

The supplied Grok report declares 70 tests passed and wheel installation/use/uninstallation verified on Linux x86-64/Python 3.12.3. Those claims concern the upstream archive. This release adds branding and packaging documentation; the complete Linux suite and installed-package workflow must be repeated for this release before a public release is accepted. Upstream execution logs and wheel were not included. No new OS support is claimed.
