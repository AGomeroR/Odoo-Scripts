# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Standalone Odoo maintenance scripts that are **not part of the Importador pipeline** (`../Importador/`). Each one is run by hand on its own. They talk to Odoo over XML-RPC and, apart from `diagnostico_duplicados.py` (read-only), they **write to Odoo**. Code, comments, logs and the README are in Spanish; keep that style.

There is no build, lint or test suite. Install the dependencies with `pip install -r requirements.txt`, or use the Importador's venv (`../Importador/venv/bin/python`).

## Running scripts

```bash
python <script>.py         # PRODUCTION: shows a warning and asks for confirmation (type 's')
python <script>.py --stg   # staging
python limpiar_duplicados.py --stg --dry-run   # limpiar_duplicados also takes --dry-run (any order)
```

To check a change without touching Odoo, run `python -m py_compile *.py`. If you need a real run, use `--stg`. Never run against production without the user's approval: the scripts rename, delete and archive real records.

## Required for every script (user requirements)

Every new or modified script must keep these behaviours:

1. **Production by default, staging with `--stg`.** Every script supports both. Get this by connecting through `comun.OdooClient`; never write a separate connection.
2. **Always flag production.** Before doing anything in production, the script shows a clear PRODUCTION warning with the URL and database and waits for confirmation (`s`). `connect_to_odoo()` already does this, so connect before any write, and don't skip or bypass the prompt.
3. **If credentials fail, ask and save.** When the credentials for the chosen environment are missing or wrong, the script asks for them in the console and writes the working ones to `.env` under the matching keys (with or without `_STG`). This is also in `connect_to_odoo()`.
4. **`.env` holds only what the scripts use.** If a script needs a new variable, add it to `.env` and the README. Remove variables that no script uses any more.
5. **AI uses the same Ollama setup as the Importador** (same model and URLs as `../Importador/.env`), but the configuration and logic **live in this folder**. Nothing reads from `../Importador` at runtime.
6. Document `--stg` (and any other flags) in the script's docstring under `Uso:` and in `README.md`.

## Architecture

- **`comun.py` is the only connection path.** Every script uses `OdooClient` from it (or a subclass, e.g. `CategoryAttributeManager` in `Actualizar_atributos_publicados.py`). When it is imported, it:
  - loads this folder's `.env`;
  - checks for `--stg` and **removes it from `sys.argv`**, so scripts parse their own flags without seeing it;
  - picks the credential keys: `ODOO_URL/ODOO_DB/ODOO_USER/ODOO_API_KEY` for production, or the same keys with the `_STG` suffix for staging.
- **`OdooClient.connect_to_odoo()`**:
  - In production, it shows a warning and asks for `s` before connecting; anything else exits with code 1.
  - If credentials are missing or fail, it asks for them in the console (the API key through `getpass`) and retries until they work.
  - It then writes the working credentials back to `.env` with `dotenv.set_key`.
- Credentials live on the client instance. Make every Odoo call through `client.execute(model, method, args, kwargs)`; don't create new `ServerProxy` objects or read the `ODOO_*` variables yourself.
- **Logging:** call `configurar_log("<name>_log.txt")` at module level, then use `log_message()`, which prints to the console and appends to `logs/<name>`. The two duplicate scripts are the exception: they only `print`.
- **Text helpers:** `normalizar()` (lowercase, no accents, collapsed spaces) is how category and value names are matched between the Odoo trees.

## Things that aren't obvious from the code

- Current `.env` keys: production and `_STG` Odoo credentials, Ollama (`OPENAI_MODEL`, `OPENAI_API_URL`, `OPENAI_API_URL_DOCKER`, optional `OPENAI_API_URL_HOST`) and `PROVEEDOR`.
- `Actualizar_atributos_publicados.py`: `MODELOS` and the AI functions (`llamar_api`, `extraer_json`, `seleccionar_valores_con_ia`, `validar_respuesta`, `sync_attributes`) are a **copy** of `../Importador/23.Atributos_categoria.py`, and `.promptatributos` is a copy of the Importador's prompt. Changes to script 23 have to be copied here by hand. It makes one AI call per product (it can take hours). It skips products that already have attributes other than Marca/Brand, so it can be re-run.
- The category scripts work in a fixed order:
  1. `Sincronizar_categorias.py` makes the internal categories (`product.category`) match the eCommerce ones (`product.public.category`).
  2. `Sincronizar_categorias_tpv.py` then rebuilds the POS tree (`pos.category`) from the internal tree. It can't delete POS categories while a POS session is open; re-run it once all sessions are closed.
- Duplicate products are detected by the custom field `x_studio_referencia_del_proveedor`. `diagnostico_duplicados.py` reads a hard-coded `EXCEL_PATH` and only looks at active products. `limpiar_duplicados.py` includes archived products, keeps the newest copy, and hard-deletes or archives the others depending on whether they have transactions.
- `README.md` describes each script step by step. Update it when a script's behaviour changes.
