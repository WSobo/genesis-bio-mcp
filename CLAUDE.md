# Genesis Bio MCP Rules

PKG: `uv` ONLY. NO pip/conda/venv. Add: `uv add [--dev] <pkg>`. Run: `uv run <cmd>`.

FS/SEARCH: NO built-in Read/Grep/Glob. NO raw ls/cat/git. MUST use bash `rtk <cmd>` (`rtk ls`, `rtk read <file>`, `rtk grep`, `rtk git`).

LINT: `uv run ruff format .` & `uv run ruff check --fix .` (ALWAYS run before commit).

ARCH:
- Type hints MANDATORY.
- Pydantic V2 ONLY (`model_dump`, `model_validate`).
- MCP tools output strictly formatted MARKDOWN strings, NEVER raw JSON dicts.
- Wrap external APIs in `safe_call` to prevent parallel pipeline failures.

TEST: `uv run pytest tests/ -v`

GIT:
- Use `rtk git` for ALL version control. 
- Commits MUST be atomic (one feature/fix per commit).
- Conventional Commits ONLY (`feat:`, `fix:`, `refactor:`, `docs:`).
- NEVER `git push` without explicit user permission.
- NEVER track/commit `data/`, `.parquet`, or `.csv` files.