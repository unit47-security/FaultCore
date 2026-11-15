# Consegna post-fix M5 script

## Modifiche a `scripts/m5_install_verify.sh`
- SRC risolto in assoluto prima di `cd`
- `set -euo pipefail` con gestione esplicita RC=1 (crash lab)
- RC≠0,1 → FAIL
- Validazione JSON: schema, outcome, usable_for_minimization, sha256 input
- Verifica entrypoint e modulo nel venv; uninstall controllato
- Solo cleanup della directory temporanea dello script
- Fallback wheel: `pip wheel --no-build-isolation` se `python -m build` fallisce (rete/isolamento)

## Test
- Collaudo reale M5: OK (Linux x86_64, Py 3.12.3)
- Path con spazi: OK
- Suite: 70 passed

## Checklist
- M4: chiusa (precedente)
- M5: **chiusa** (install/uninstall verificati)

## Originale
`FaultCore-safe-write.zip` non modificato.
