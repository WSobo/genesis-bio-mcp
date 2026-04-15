"""MaveDB deep mutational scanning (DMS) client.

MaveDB (https://www.mavedb.org) is the canonical repository for DMS experiments.
Each "score set" maps thousands of single-amino-acid substitutions or indels to a
quantitative fitness/function score measured in a defined cellular assay.  When a
DMS dataset exists for a target protein it provides the highest-resolution
residue-level tolerance-to-mutation signal available — superior to evolutionary
constraint metrics for predicting the functional consequence of any single variant.

This client searches MaveDB by gene symbol using the text search endpoint and
returns available score-set metadata (URNs, variant counts, publication references).
Individual variant-level scores can be retrieved from MaveDB using the returned URNs.

No API key required.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from genesis_bio_mcp.models import DMSResults, DMSScoreSet

logger = logging.getLogger(__name__)

_MAVEDB_BASE = "https://api.mavedb.org/api/v1"
_SEARCH_URL = f"{_MAVEDB_BASE}/score-sets/search"
_SEMAPHORE = asyncio.Semaphore(3)


class MaveDBClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._cache: dict[str, DMSResults] = {}

    async def get_dms_scores(self, gene_symbol: str) -> DMSResults | None:
        """Search MaveDB for DMS score sets associated with a gene.

        Uses text search (POST /score-sets/search) which matches against score set
        titles and descriptions.  Returns metadata for all matching score sets sorted
        by variant count descending.  Returns an empty DMSResults (not None) when no
        datasets are found — the absence of DMS data is itself informative.

        Args:
            gene_symbol: HGNC gene symbol, e.g. ``'BRCA1'``.

        Returns:
            :class:`DMSResults` or ``None`` on network error.
        """
        symbol = gene_symbol.strip().upper()
        if symbol in self._cache:
            logger.debug("MaveDB cache hit: %s", symbol)
            return self._cache[symbol]

        async with _SEMAPHORE:
            result = await self._fetch(symbol)

        if result is not None:
            self._cache[symbol] = result
        return result

    async def _fetch(self, symbol: str) -> DMSResults | None:
        payload = {"text": symbol}
        try:
            resp = await self._client.post(_SEARCH_URL, json=payload, timeout=25.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("MaveDB fetch failed for '%s': %s", symbol, exc)
            return None

        if not isinstance(data, dict):
            logger.warning("MaveDB unexpected response type for '%s': %s", symbol, type(data))
            return None

        items: list[dict] = data.get("scoreSets", [])

        if not items:
            return DMSResults(
                gene_symbol=symbol,
                total_score_sets=0,
                total_variants=0,
                score_sets=[],
            )

        score_sets: list[DMSScoreSet] = []
        for item in items:
            urn = item.get("urn") or ""
            if not urn:
                continue

            title = item.get("title") or urn
            short_desc = item.get("shortDescription") or None
            num_variants = int(item.get("numVariants") or 0)
            published_date = item.get("publishedDate") or None

            # Extract target gene / UniProt from targetGenes list
            target_genes: list[dict] = item.get("targetGenes") or []
            target_gene_sym: str | None = None
            uniprot_acc: str | None = None
            if target_genes:
                first = target_genes[0]
                target_gene_sym = first.get("name") or None
                uniprot_acc = first.get("uniprotIdFromMappedMetadata") or None

            # Extract PMID / DOI from primaryPublicationIdentifiers
            pmid: str | None = None
            doi: str | None = None
            for pub in item.get("primaryPublicationIdentifiers") or []:
                db_name = (pub.get("dbName") or "").lower()
                identifier = pub.get("identifier") or ""
                if db_name == "pubmed" and not pmid:
                    pmid = str(identifier)
                elif db_name == "doi" and not doi:
                    doi = str(identifier)

            score_sets.append(
                DMSScoreSet(
                    urn=urn,
                    title=title,
                    short_description=short_desc,
                    num_variants=num_variants,
                    target_gene=target_gene_sym,
                    uniprot_accession=uniprot_acc,
                    published_date=published_date,
                    pmid=pmid,
                    doi=doi,
                )
            )

        # Sort by variant count descending — larger sets = more complete coverage
        score_sets.sort(key=lambda s: s.num_variants, reverse=True)
        total_variants = sum(s.num_variants for s in score_sets)

        return DMSResults(
            gene_symbol=symbol,
            total_score_sets=len(score_sets),
            total_variants=total_variants,
            score_sets=score_sets,
        )
