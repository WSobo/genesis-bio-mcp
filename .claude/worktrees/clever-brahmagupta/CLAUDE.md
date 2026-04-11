# Genesis Bio MCP Rules

## Package Management
PKG: `uv` ONLY. NO pip/conda/venv. Add: `uv add [--dev] <pkg>`. Run: `uv run <cmd>`.

## Filesystem & Search
NO built-in Read/Grep/Glob. NO raw ls/cat/git. MUST use bash `rtk <cmd>` (`rtk ls`, `rtk read <file>`, `rtk grep`, `rtk git`).

## Linting
`uv run ruff format .` & `uv run ruff check --fix .` (ALWAYS run before commit).

## Architecture
- Type hints MANDATORY.
- Pydantic V2 ONLY (`model_dump`, `model_validate`).
- MCP tools output strictly formatted MARKDOWN strings, NEVER raw JSON dicts.
- Wrap external APIs in `safe_call` to prevent parallel pipeline failures.
- All tools must include annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).
- Tool names: `{service}_{action}_{resource}` snake_case format.
- Support `response_format` param (`markdown` default, `json` for programmatic use).

## MCP References
- Python MCP patterns: docs/python_mcp_server.md
- MCP best practices: docs/mcp_best_practices.md

## Testing
`uv run pytest tests/ -v`

## Git
- Use `rtk git` for ALL version control.
- Commits MUST be atomic (one feature/fix per commit).
- Conventional Commits ONLY (`feat:`, `fix:`, `refactor:`, `docs:`).
- NEVER `git push` without explicit user permission.
- NEVER track/commit `data/`, `.parquet`, or `.csv` files.