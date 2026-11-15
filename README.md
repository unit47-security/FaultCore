# FaultCore

```text
 _____ _   _   _ _   _____ ____ ___  ____  _____
|  ___/ \ | | | | | |_   _/ ___/ _ \|  _ \| ____|
| |_ / _ \| | | | |   | || |  | | | | |_) |  _|
|  _/ ___ \ |_| | |___| || |__| |_| |  _ <| |___
|_|/_/   \_\___/|_____|_| \____\___/|_| \_\_____|

UNIT-47 | Reproduce. Minimize. Document.
```

**v0.1.0 — laboratory MVP.** Local execution, reproducibility checks, signature-preserving input minimization and JSON/text reports. A matching signature is not proof of a shared root cause; a crash is not a confirmed vulnerability.

## Install

Python 3.10+; Linux is the primary platform. From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
faultcore --help
faultcore --banner
```

Run `faultcore` for help and the ASCII logo. On an interactive terminal, human-readable commands show the logo automatically. Use the global `--no-banner` before a subcommand to hide it. `--json` output stays machine-readable. No colors or terminal control sequences are required.

## Quick start: bundled laboratory fixture

Run these commands from this source directory after installation:

```bash
printf 'PADPADCRASHMEPADPAD' > seed.bin
faultcore reproduce --arg "$(command -v python)" --arg fixtures/crash_on_pattern.py --input-file seed.bin --runs 3
faultcore minimize --arg "$(command -v python)" --arg fixtures/crash_on_pattern.py --input-file seed.bin --output seed.min --report-json min.json --report-text min.txt
```

The fixture deliberately exits with code 77 when it sees `CRASHME`. A direct `run` returns CLI code 1 for that expected outcome. `reproduce` returns 0 for a stable result. See the report status: an incomplete minimization can also return 0 and must not be treated as completed solely from the CLI exit code.

## Commands

- `run`: execute once with timeout and bounded captured output.
- `reproduce`: repeat and compare signatures.
- `minimize`: delete input blocks while preserving the required signature.
- `--banner`, `--version`, `--help`: identity, version and usage.

Prefer repeated `--arg` values. For arguments beginning with a hyphen, use `--arg=VALUE`. No shell is invoked. Outcomes: `normal`, `exit_nonzero`, `signal_crash`, `timeout`, `prep_error`. Nonzero exits need a recognized lab marker to qualify for minimization.

Reports use schema **1.1.0**, with hashes, execution details, platform, limits and final verification state. Raw inputs, complete environment and raw streams are not exported by default; command arguments and diagnostic markers can still contain sensitive information. Review reports before sharing.

Existing outputs are refused unless `--overwrite` is explicit. Inputs and identifiable program files remain protected. Exclusive publication requires filesystem hard-link support; unsupported filesystems fail rather than use an unsafe replacement fallback.

## Limits

- Tested scope: bundled laboratory fixtures. This is not an OS sandbox.
- Timeouts, output limits and process groups do not isolate files or networking.
- Detached descendants may escape process-group termination.
- Normalization is heuristic; conservative mode preserves hexadecimal values.
- `complete` requires finished search and successful final verification. `incomplete` and `final_failed` are distinct.
- Minimization does not guarantee a global minimum. Cleanup time may exceed the operational budget.
- Ressembly export is a documented placeholder, not a working integration.
- Upstream reports 70 tests and Linux x86-64 installation validation; local release checks are recorded separately in `docs/RELEASE-VALIDATION.md`. No general macOS/Windows support claim.

## Development

```bash
python -m pip install '.[dev]'
python -m pytest -q
```

See `CHANGELOG.md`, `SECURITY.md` and `docs/RELEASE-VALIDATION.md`. License: MIT.
