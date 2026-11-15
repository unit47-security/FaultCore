# M5 — report finale (chiusura)

## Collaudo reale eseguito

| Passo | Risultato |
|-------|-----------|
| `python3 -m venv --without-pip` | OK |
| Bootstrap pip (get-pip) | OK (con retry su 502 proxy) |
| Build wheel (`build` o `pip wheel --no-build-isolation`) | OK → `faultcore-0.1.0-py3-none-any.whl` |
| `pip install` wheel in venv | OK |
| `faultcore --help` da PATH venv | OK |
| Esecuzione da directory esterna, `PYTHONPATH` unset | OK |
| Modulo e entrypoint risolti dentro il venv | OK |
| Fixture crash → CLI exit **1** | OK |
| Validazione `run.json` (schema 1.1.0, outcome, usable, sha256) | OK |
| `pip uninstall` + entrypoint assente + import fallisce | OK |
| Path sorgenti **con spazi** | OK |

**Piattaforma:** Linux x86_64, Python 3.12.3
**Script:** `scripts/m5_install_verify.sh`
**Exit script collaudo:** 0

## Suite unitaria
`70 passed` (regressioni write/budget/CLI incluse)

## Criteri

| Criterio | Stato |
|----------|--------|
| Build + install venv + uso esterno + uninstall | **superato** |
| Exit 1 atteso gestito con `set -e` | **superato** |
| Report JSON validato | **superato** |
| Path con spazi | **superato** |
| Codice sorgente errato → fallimento script | **superato** |

**M5 chiusa.**
