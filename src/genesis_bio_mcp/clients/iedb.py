"""IEDB (Immune Epitope Database) B-cell epitope client.

IEDB is the canonical repository for published immune epitope data, covering
B-cell epitopes (antibody binding sites), T-cell epitopes, and MHC binding data.

This client queries B-cell epitope assays by antigen name, returning positive
epitope records with their sequences, antibody isotypes, and publication/
structural evidence.  Understanding the known epitope landscape for an antigen
is essential for antibody design: it identifies well-characterized regions
(targets for existing antibodies) and potential gaps.

No API key required.  Uses the IEDB PostgREST query API.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import httpx

from genesis_bio_mcp.models import EpitopeRecord, EpitopeResults

logger = logging.getLogger(__name__)

_IEDB_API = "https://query-api.iedb.org/api/v1/bcell_search"
_SEMAPHORE = asyncio.Semaphore(3)

_SELECT_FIELDS = ",".join(
    [
        "structure_description",
        "linear_sequence",
        "qualitative_measure",
        "antibody_isotype",
        "pubmed_id",
        "pdb_id",
        "curated_source_antigen",
    ]
)


class IEDBClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._cache: dict[str, EpitopeResults] = {}

    async def get_epitopes(
        self,
        antigen_query: str,
        max_results: int = 200,
    ) -> EpitopeResults | None:
        """Return positive B-cell epitope records from IEDB for the given antigen.

        Searches the IEDB B-cell assay table by antigen description (case-insensitive
        substring match).  Returns only positive assays.  Results are deduplicated by
        epitope sequence/description; the tool returns a summary with epitope counts,
        structural evidence, and key epitope sequences.

        Args:
            antigen_query: Free-text antigen name. For best results use the full protein
                name (e.g. ``'epidermal growth factor receptor'``) rather than the gene
                symbol (e.g. ``'EGFR'``).
            max_results: Maximum number of raw assay records to retrieve (default 200).

        Returns:
            :class:`EpitopeResults` or ``None`` on network error.
        """
        key = antigen_query.strip().lower()
        if key in self._cache:
            logger.debug("IEDB cache hit: %s", key)
            return self._cache[key]

        async with _SEMAPHORE:
            result = await self._fetch(antigen_query, max_results)

        if result is not None:
            self._cache[key] = result
        return result

    async def _fetch(self, antigen_query: str, max_results: int) -> EpitopeResults | None:
        query = antigen_query.strip()
        encoded = quote(query, safe="")
        params = {
            "antigen_description": f"ilike.*{query}*",
            "qualitative_measure": "eq.Positive",
            "limit": str(max_results),
            "select": _SELECT_FIELDS,
        }
        try:
            resp = await self._client.get(_IEDB_API, params=params, timeout=25.0)
            resp.raise_for_status()
            records = resp.json()
        except Exception as exc:
            logger.warning("IEDB fetch failed for '%s': %s", encoded, exc)
            return None

        if not isinstance(records, list):
            logger.warning("IEDB unexpected response type for '%s': %s", encoded, type(records))
            return None

        if not records:
            return EpitopeResults(
                antigen_query=query,
                total_assays=0,
                unique_epitopes=0,
                with_structure=0,
                epitopes=[],
            )

        # Deduplicate by epitope description/sequence
        seen: dict[str, EpitopeRecord] = {}
        with_structure = 0

        for rec in records:
            seq = rec.get("linear_sequence") or rec.get("structure_description") or ""
            if not seq:
                continue

            isotype = rec.get("antibody_isotype") or None
            pmid = str(rec.get("pubmed_id")) if rec.get("pubmed_id") else None
            pdb_id = rec.get("pdb_id") or None

            ag = rec.get("curated_source_antigen") or {}
            antigen_name = ag.get("name") or None
            antigen_accession = ag.get("accession") or None
            start = ag.get("starting_position")
            end = ag.get("ending_position")

            if pdb_id:
                with_structure += 1

            if seq not in seen:
                seen[seq] = EpitopeRecord(
                    sequence=seq,
                    isotype=isotype,
                    pmid=pmid,
                    pdb_id=pdb_id,
                    antigen_name=antigen_name,
                    antigen_accession=antigen_accession,
                    start_position=int(start) if start is not None else None,
                    end_position=int(end) if end is not None else None,
                )

        epitopes = list(seen.values())

        return EpitopeResults(
            antigen_query=query,
            total_assays=len(records),
            unique_epitopes=len(epitopes),
            with_structure=with_structure,
            epitopes=epitopes[:50],
        )
