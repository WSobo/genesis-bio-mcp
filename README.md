# genesis-bio-mcp

An MCP (Model Context Protocol) server that connects AI agents to major public biomedical databases for drug discovery target prioritization.

Ask an AI agent *"Assess BRAF as an oncology target for melanoma"* and watch it autonomously chain queries across UniProt, Open Targets, DepMap, GWAS Catalog, PubChem, and ChEMBL into a structured evidence report — no hardcoded scripts, no manual API calls.

## Tools

| Tool | Database | Purpose |
|------|----------|---------|
| `resolve_gene` | UniProt + NCBI | Resolve gene aliases → canonical HGNC symbol, NCBI ID, UniProt accession |
| `get_protein_info` | UniProt Swiss-Prot | Protein function, pathways, disease variants, PDB structures |
| `get_target_disease_association` | Open Targets | Evidence-based association score (0–1) for a target–disease pair |
| `get_cancer_dependency` | DepMap + Open Targets | CRISPR essentiality scores across cancer cell lines |
| `get_gwas_evidence` | GWAS Catalog | Genome-wide significant SNP associations for a trait |
| `get_compounds` | PubChem | Active small molecules with bioactivity data |
| `prioritize_target` | All of the above + ChEMBL | Full parallel evidence synthesis → priority score (0–10) + report |
| `compare_targets` | All of the above + ChEMBL | Rank 2–5 targets side by side for an indication |

## Quickstart

### Install

```bash
git clone https://github.com/WSobo/genesis-bio-mcp
cd genesis-bio-mcp
uv sync
```

### Add to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "genesis-bio-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/genesis-bio-mcp", "genesis-bio-mcp"]
    }
  }
}
```

Replace `/path/to/genesis-bio-mcp` with the absolute path to your clone. Restart Claude Desktop. You'll see the genesis-bio-mcp tools available in your conversation.

### Try it

Ask Claude:
- *"Is PCSK9 a good target for cardiovascular disease? Check Open Targets and PubChem."*
- *"Assess BRAF as an oncology target for melanoma — full report."*
- *"What GWAS evidence links FTO to obesity?"*
- *"Compare BRAF, EGFR, and KRAS for non-small cell lung cancer."*

## Example Output

```
prioritize_target("BRAF", "melanoma")

→ priority_score: 7.11 / 10
→ priority_tier: High
→ evidence_summary: "BRAF shows strong Open Targets association with melanoma (score: 0.82,
  n=5 evidence items). Open Targets reports strong known-drug evidence for BRAF (score: 0.98),
  suggesting existing approved or clinical-stage therapeutics — likely biologics if
  small-molecule data is sparse. DepMap CRISPR data show dependency in 9% of cancer lines,
  highest in cancer or benign tumor. ChEMBL reports 68 compounds with potency data against
  BRAF; best IC50 ≈ 0.3 nM (clinical-grade, pChEMBL=9.5)."

→ disease_association.overall_score:          0.82
→ disease_association.somatic_mutation_score: 0.80   # BRAF V600E is a major somatic driver
→ disease_association.known_drug_score:       0.98   # vemurafenib, dabrafenib (Open Targets clinical evidence)
→ cancer_dependency.fraction_dependent_lines: 0.095  # 115/1208 lines — selective, not pan-essential
→ cancer_dependency.data_source: "DepMap Chronos Combined (115/1208 cell lines dependent)"
→ gwas_evidence: null                                # expected — see note below
→ chembl_compounds.best_pchembl: 9.52
→ chembl_compounds.total_active_compounds: 68
→ data_gaps: ["gwas"]
```

**Score breakdown:** OT (0.82×3=2.46) + DepMap real (0.095×2=0.19) + clinical (0.98×1.5=1.46) + ChEMBL pChEMBL≥9 (1.5) + protein (reviewed+variants=1.5) = **7.11**.

> **Note on GWAS for BRAF/melanoma**: BRAF V600E is a somatic driver mutation (~50% of melanomas), not a germline susceptibility variant. GWAS Catalog correctly returns no melanoma-trait hits near BRAF. The server reports this as a `data_gap` rather than returning off-topic hits. For germline-driven targets like *FTO* (obesity) or *PCSK9* (cardiovascular disease), GWAS evidence will be strongly populated.

## Scoring Model

The composite priority score (0–10) combines six evidence axes:

| Source | Max | Logic |
|--------|-----|-------|
| Open Targets association | 3.0 | `overall_score × 3` |
| DepMap CRISPR dependency | 2.0 | `fraction_dependent × 2` (×0.7 confidence discount if OT proxy used) |
| GWAS evidence | 2.0 | `min(hits, 10) / 10 × 2` |
| Clinical / known-drug evidence | 1.5 | `known_drug_score × 1.5` — separates approved-drug targets from literature-only |
| ChEMBL potency | 1.5 | pChEMBL ≥9 → 1.5, ≥7 → 1.0, ≥5 → 0.5, else 0.25 (falls back to PubChem count if ChEMBL absent) |
| UniProt protein quality | 1.5 | reviewed (+0.5) + variant coverage (max +1.0) |

Pan-essential genes (common_essential in DepMap) have their DepMap contribution capped at 0.5 to reflect narrow therapeutic windows. Clinical evidence is sourced from Open Targets `clinical` datatype (covers approved drugs and clinical-stage compounds).

## Architecture

```
src/genesis_bio_mcp/
├── server.py                  # FastMCP server, tool registration, shared httpx client
├── models.py                  # Pydantic V2 output models (all fields documented for agents)
├── clients/
│   ├── uniprot.py             # UniProt REST: gene_exact query, Swiss-Prot parsing
│   ├── open_targets.py        # Open Targets GraphQL: 3-step resolution (gene→Ensembl, disease→EFO, assoc)
│   ├── depmap.py              # DepMap task API (Celery polling) + persistent disk cache + OT lineage fallback
│   ├── gwas.py                # GWAS Catalog HAL/REST, Unicode normalization, trait filtering
│   ├── pubchem.py             # PubChem REST, asyncio.Semaphore rate limiting, tenacity retries
│   └── chembl.py              # ChEMBL REST: target lookup + IC50/Ki/Kd potency data (pChEMBL values)
└── tools/
    ├── gene_resolver.py       # Multi-source alias resolution (UniProt primary, NCBI E-utils for gene ID)
    └── target_prioritization.py  # asyncio.gather orchestration, safe_call error isolation, score computation
```

**Key design decisions:**
- Single shared `httpx.AsyncClient` via FastMCP `lifespan` for connection pooling
- `asyncio.gather` in `prioritize_target` runs 6 database APIs simultaneously
- Every sub-query wrapped in `_safe()` — agent never crashes on a single API failure
- DepMap CSV cached to `data/depmap_cache.csv` (7-day TTL) — warm starts load in <1s instead of 30–60s
- ChEMBL potency-based scoring replaces PubChem binary Active/Inactive counting — a target with one 1 nM inhibitor scores higher than 100 micromolar hits
- Opinionated output filtering — agents see 8 meaningful fields, not raw 50-field API blobs
- Agent-readable tool docstrings that explain *when* to use each tool and *how* to format inputs

## Development

```bash
uv sync
uv run pytest tests/ -v
uv run pytest tests/ -v --cov=genesis_bio_mcp

# Run full integration test against live APIs (saves reports to examples/)
uv run python test_full.py

# Single target
uv run python test_full.py BRAF melanoma
```

### Running the server directly

```bash
uv run genesis-bio-mcp          # stdio transport (for MCP clients)
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NCBI_EMAIL` | `genesis-bio-mcp@example.com` | Required for NCBI E-utils polite use |

## API Notes

| Database | API Type | Rate Limit | Notes |
|----------|----------|------------|-------|
| UniProt | REST | Generous | Filter `organism_id:9606` + `reviewed:true` for human Swiss-Prot |
| Open Targets | GraphQL | Generous (~2s latency) | Requires Ensembl ID and EFO ID — resolved automatically |
| DepMap | REST (task queue) | Moderate | Uses Celery task polling: POST `/download/custom` → poll `/task/{id}` until SUCCESS → fetch pre-signed CSV URL. Results cached to `data/depmap_cache.csv` (7-day TTL) to avoid re-downloading 10+ MB on every cold start. Falls back to Open Targets somatic mutation proxy if DepMap is unreachable. |
| GWAS Catalog | REST/HAL | Moderate | HAL JSON; Unicode normalization required for trait matching |
| PubChem | REST | 5 req/sec | Returns HTTP 503 on rate limit; handled with tenacity + Semaphore. Used as fallback when ChEMBL has no data. |
| ChEMBL | REST | ~1 req/sec | No API key required. Two-step: target search → bioactivity query filtered to IC50/Ki/Kd/EC50 with a pChEMBL value. `asyncio.Semaphore(2)` prevents rate limit breaches. |
| NCBI E-utils | REST | 3 req/sec | Requires `email` parameter; set `NCBI_EMAIL` env var |

## License

MIT
