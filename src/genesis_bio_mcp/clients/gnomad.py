"""gnomAD constraint client.

gnomAD (Genome Aggregation Database) provides population genetics data from
>125,000 exomes and >15,000 whole genomes.  This client fetches gene-level
constraint metrics that quantify how much a gene tolerates loss-of-function
(LoF) and missense mutations in the human population — a critical pre-filter
for protein engineering campaigns.

Key metrics:
    pLI     — probability of being loss-of-function intolerant (>0.9 = intolerant)
    oe_lof  — observed/expected LoF variant ratio (lower = more constrained)
    LOEUF   — oe_lof_upper confidence interval bound (most used constraint metric)
    oe_mis  — observed/expected missense ratio
    lof_z   — LoF Z-score (>3 = significant constraint)
    mis_z   — missense Z-score

Data source: gnomAD v4 via public GraphQL API.  No API key required.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from genesis_bio_mcp.models import GnomADConstraint

logger = logging.getLogger(__name__)

_GNOMAD_API = "https://gnomad.broadinstitute.org/api"
_SEMAPHORE = asyncio.Semaphore(3)

_CONSTRAINT_QUERY = """
query GeneConstraint($symbol: String!) {
  gene(gene_symbol: $symbol, reference_genome: GRCh38) {
    gene_id
    name
    canonical_transcript_id
    gnomad_constraint {
      pLI
      lof_z
      mis_z
      oe_lof
      oe_lof_lower
      oe_lof_upper
      oe_mis
      exp_lof
      exp_mis
      obs_lof
      obs_mis
    }
  }
}
"""


class GnomADClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._cache: dict[str, GnomADConstraint] = {}

    async def get_constraint(self, gene_symbol: str) -> GnomADConstraint | None:
        """Return gnomAD constraint metrics for *gene_symbol*.

        Returns ``None`` if the gene is not found or the API is unavailable.
        Returns a :class:`GnomADConstraint` with ``constraint_available=False``
        if the gene exists but has no constraint data (e.g. insufficiently covered).
        """
        symbol = gene_symbol.strip().upper()
        if symbol in self._cache:
            logger.debug("gnomAD cache hit: %s", symbol)
            return self._cache[symbol]

        async with _SEMAPHORE:
            result = await self._fetch(symbol)

        if result is not None:
            self._cache[symbol] = result
        return result

    async def _fetch(self, symbol: str) -> GnomADConstraint | None:
        try:
            resp = await self._client.post(
                _GNOMAD_API,
                json={"query": _CONSTRAINT_QUERY, "variables": {"symbol": symbol}},
                timeout=20.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("gnomAD fetch failed for %s: %s", symbol, exc)
            return None

        errors = data.get("errors")
        if errors:
            logger.warning("gnomAD GraphQL error for %s: %s", symbol, errors)
            return None

        gene = (data.get("data") or {}).get("gene")
        if not gene:
            logger.info("gnomAD: no gene record for '%s'", symbol)
            return None

        constraint = gene.get("gnomad_constraint")
        if not constraint:
            return GnomADConstraint(
                gene_symbol=symbol,
                ensembl_id=gene.get("gene_id"),
                gene_name=gene.get("name"),
                constraint_available=False,
            )

        def _f(key: str) -> float | None:
            v = constraint.get(key)
            return float(v) if v is not None else None

        def _i(key: str) -> int | None:
            v = constraint.get(key)
            return int(v) if v is not None else None

        return GnomADConstraint(
            gene_symbol=symbol,
            ensembl_id=gene.get("gene_id"),
            gene_name=gene.get("name"),
            constraint_available=True,
            pLI=_f("pLI"),
            lof_z=_f("lof_z"),
            mis_z=_f("mis_z"),
            oe_lof=_f("oe_lof"),
            oe_lof_lower=_f("oe_lof_lower"),
            oe_lof_upper=_f("oe_lof_upper"),
            oe_mis=_f("oe_mis"),
            exp_lof=_f("exp_lof"),
            exp_mis=_f("exp_mis"),
            obs_lof=_i("obs_lof"),
            obs_mis=_i("obs_mis"),
        )
