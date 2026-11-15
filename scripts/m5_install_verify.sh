#!/usr/bin/env bash
# M5 install / uninstall verification for FaultCore.
# Usage: bash scripts/m5_install_verify.sh /path/to/src
set -euo pipefail

die() { echo "FAIL: $*" >&2; exit 1; }
log() { echo ">> $*"; }

SRC_ARG="${1:?usage: $0 /path/to/src (directory with pyproject.toml)}"
SRC="$(cd -- "$SRC_ARG" && pwd)" || die "cannot resolve source path: $SRC_ARG"
[[ -f "$SRC/pyproject.toml" ]] || die "no pyproject.toml in $SRC"
[[ -f "$SRC/fixtures/crash_on_pattern.py" ]] || die "missing lab fixture in $SRC/fixtures"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/faultcore-m5-XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

log "platform=$(uname -s) arch=$(uname -m) python=$(python3 --version 2>&1)"
log "SRC=$SRC"
log "WORK=$WORK"

log "creating clean venv (--without-pip)"
python3 -m venv --without-pip "$WORK/venv" || die "venv creation failed"
# shellcheck disable=SC1091
source "$WORK/venv/bin/activate"
VENV_PY="$WORK/venv/bin/python"
[[ -x "$VENV_PY" ]] || die "venv python missing"

bootstrap_pip() {
  if "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    return 0
  fi
  log "bootstrapping pip into venv"
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$WORK/get-pip.py" \
    || die "cannot download get-pip.py"
  local i
  for i in 1 2 3 4 5; do
    if "$VENV_PY" "$WORK/get-pip.py"; then
      return 0
    fi
    log "get-pip retry $i"
    sleep 2
  done
  die "get-pip failed after retries"
}
bootstrap_pip

pip_retry() {
  local i
  for i in 1 2 3 4 5 6 7 8; do
    if "$VENV_PY" -m pip "$@"; then
      return 0
    fi
    log "pip retry $i: $*"
    sleep 2
  done
  return 1
}

log "installing build tools"
pip_retry install -U pip wheel build || die "cannot install pip/wheel/build"

log "building wheel"
mkdir -p "$WORK/dist"
if ! "$VENV_PY" -m build --wheel --outdir "$WORK/dist" "$SRC"; then
  log "isolated build failed; trying pip wheel --no-build-isolation"
  pip_retry install -U setuptools wheel || die "cannot install setuptools/wheel"
  if ! "$VENV_PY" -m pip wheel --no-build-isolation --no-deps -w "$WORK/dist" "$SRC"; then
    die "wheel build failed"
  fi
fi
WHEEL="$(ls "$WORK/dist"/faultcore-*.whl 2>/dev/null || true)"
[[ -n "$WHEEL" ]] || die "no wheel produced in $WORK/dist"
log "wheel=$WHEEL"

log "installing wheel into venv"
pip_retry install "$WHEEL" || die "pip install failed"

FC_BIN="$WORK/venv/bin/faultcore"
[[ -x "$FC_BIN" ]] || die "entrypoint $FC_BIN missing after install"

EXT="$WORK/external"
mkdir -p "$EXT/fixtures"
cp "$SRC/fixtures/crash_on_pattern.py" "$EXT/fixtures/"
printf 'CRASHME' > "$EXT/seed.bin"

log "running installed faultcore from $EXT (PYTHONPATH cleared)"
cd "$EXT"
unset PYTHONPATH || true
export PATH="$WORK/venv/bin:$PATH"
hash -r 2>/dev/null || true

CMD_PATH="$(command -v faultcore)" || die "faultcore not on PATH"
[[ "$CMD_PATH" == "$FC_BIN" ]] || die "faultcore resolved to $CMD_PATH, expected $FC_BIN"
MOD_PATH="$("$VENV_PY" -c 'import faultcore, os; print(os.path.dirname(faultcore.__file__))')"
case "$MOD_PATH" in
  "$WORK/venv"/*) log "module path ok: $MOD_PATH" ;;
  *) die "faultcore imported from outside venv: $MOD_PATH" ;;
esac

"$FC_BIN" --help >/dev/null || die "faultcore --help failed"

set +e
"$FC_BIN" run \
  --arg "$VENV_PY" \
  --arg "$EXT/fixtures/crash_on_pattern.py" \
  --input-file "$EXT/seed.bin" \
  --timeout 2 \
  --report-json "$EXT/run.json" \
  --report-text "$EXT/run.txt"
RC=$?
set -e

if [[ "$RC" -eq 1 ]]; then
  log "CLI exit code 1 as expected for lab crash fixture"
elif [[ "$RC" -eq 0 ]]; then
  die "unexpected success (exit 0) for crash fixture"
else
  die "unexpected CLI exit code $RC (expected 1 for exit_nonzero crash)"
fi

[[ -f "$EXT/run.json" ]] || die "report JSON missing"
[[ -f "$EXT/run.txt" ]] || die "report text missing"

log "validating report JSON fields"
"$VENV_PY" - "$EXT/run.json" "$EXT/seed.bin" <<'PY'
import hashlib, json, sys
from pathlib import Path
report_path, seed_path = Path(sys.argv[1]), Path(sys.argv[2])
data = json.loads(report_path.read_text())
errs = []
if data.get("schema_version") != "1.1.0":
    errs.append(f"schema_version={data.get('schema_version')!r} expected '1.1.0'")
result = data.get("result") or {}
if result.get("outcome") != "exit_nonzero":
    errs.append(f"outcome={result.get('outcome')!r} expected exit_nonzero")
sig = data.get("signature") or {}
if not sig.get("usable_for_minimization"):
    errs.append("signature.usable_for_minimization is not true")
inp = data.get("input") or {}
expected = hashlib.sha256(seed_path.read_bytes()).hexdigest()
if inp.get("sha256") != expected:
    errs.append(f"input.sha256 mismatch: {inp.get('sha256')} != {expected}")
if errs:
    print("report validation failed:", *errs, sep="\n  ", file=sys.stderr)
    sys.exit(1)
print("report validation ok")
PY

log "uninstalling faultcore from venv"
"$VENV_PY" -m pip uninstall -y faultcore || die "pip uninstall failed"
hash -r 2>/dev/null || true

if [[ -e "$FC_BIN" ]]; then
  die "entrypoint still present after uninstall: $FC_BIN"
fi
if "$VENV_PY" -c "import faultcore" 2>/dev/null; then
  die "faultcore still importable in venv after uninstall"
fi
if command -v faultcore >/dev/null 2>&1; then
  LEFT="$(command -v faultcore)"
  case "$LEFT" in
    "$WORK/venv"/*) die "venv still exposes faultcore at $LEFT" ;;
    *) log "note: faultcore exists outside this venv at $LEFT (pre-existing; not a failure)" ;;
  esac
fi

log "M5 install/uninstall verification OK"
