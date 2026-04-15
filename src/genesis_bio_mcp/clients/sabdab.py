"""SAbDab (Structural Antibody Database) client.

SAbDab is a curated database of antibody and nanobody (VHH) crystal structures
from the PDB, maintained by the Oxford Protein Informatics Group (OPIG).

Data access: the summary endpoint returns the full ~20 K row TSV for all structures.
The client caches this locally and filters in-memory to avoid re-downloading on every
tool call.  Cache TTL defaults to 7 days (configurable via GENESIS_SABDAB_CACHE_TTL_SECS).
No API key required.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time

import httpx

from genesis_bio_mcp.config.settings import settings
from genesis_bio_mcp.models import AntibodyStructure, AntibodyStructures

logger = logging.getLogger(__name__)

_SUMMARY_URL = "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/sabdab/summary/all/"
_SEMAPHORE = asyncio.Semaphore(2)

# Fields searched when filtering by query string (case-insensitive substring)
_SEARCH_FIELDS = ("antigen_name", "compound", "antigen_het_name")

# Species strings that indicate a camelid VHH source
_CAMELID_TERMS = ("lama", "camel", "alpaca", "vicugna", "dromedary")


def _parse_tsv(raw: bytes) -> list[dict[str, str]]:
    """Parse SAbDab summary TSV bytes into a list of row dicts."""
    try:
        text = raw.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        return [dict(row) for row in reader]
    except Exception as exc:
        logger.warning("SAbDab TSV parse failed: %s", exc)
        return []


def _is_nanobody(row: dict[str, str]) -> bool:
    """Return True if the row represents a VHH/nanobody chain (no light chain)."""
    lchain = row.get("Lchain", "").strip().upper()
    return lchain in ("NA", "NONE", "")


def _parse_resolution(val: str) -> float | None:
    try:
        f = float(val)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_float(val: str) -> float | None:
    try:
        f = float(val)
        return f if f != 0 else None
    except (ValueError, TypeError):
        return None


def _row_to_structure(row: dict[str, str]) -> AntibodyStructure:
    nanobody = _is_nanobody(row)
    heavy_species = row.get("heavy_species", "").strip() or None
    light_species = row.get("light_species", "").strip() or None
    affinity_val = row.get("affinity", "None")
    return AntibodyStructure(
        pdb=row.get("pdb", "").strip().upper(),
        is_nanobody=nanobody,
        antigen_name=row.get("antigen_name", "").strip() or None,
        resolution_ang=_parse_resolution(row.get("resolution", "")),
        method=row.get("method", "").strip() or None,
        heavy_species=heavy_species,
        light_species=None if nanobody else light_species,
        heavy_subclass=row.get("heavy_subclass", "").strip() or None,
        light_subclass=None if nanobody else (row.get("light_subclass", "").strip() or None),
        is_engineered=(row.get("engineered", "False").strip().lower() == "true"),
        is_scfv=(row.get("scfv", "False").strip().lower() == "true"),
        affinity_nM=_parse_float(affinity_val) if affinity_val not in ("None", "NA", "") else None,
        compound=row.get("compound", "").strip() or None,
        date_added=row.get("date", "").strip() or None,
        pmid=row.get("pmid", "").strip()
        if row.get("pmid", "").strip() not in ("None", "NA", "")
        else None,
    )


class SAbDabClient:
    """Client for the SAbDab structural antibody database.

    Downloads and caches the full SAbDab summary TSV on first use; subsequent
    queries filter the in-memory database without additional network calls until
    the cache TTL expires.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._db: list[dict[str, str]] | None = None
        self._db_loaded_at: float = 0.0

    async def get_antibody_structures(
        self,
        query: str,
        max_results: int = 20,
    ) -> AntibodyStructures | None:
        """Return antibody/nanobody structures from SAbDab for the given antigen query.

        Searches antigen_name, compound, and antigen_het_name fields (case-insensitive
        substring).  Results sorted by resolution (best first); NA resolution entries
        placed last.

        Args:
            query: Antigen gene symbol or name, e.g. ``"EGFR"`` or ``"epidermal growth factor"``.
            max_results: Maximum number of structures to return (default 20).

        Returns:
            :class:`AntibodyStructures` or ``None`` on download failure.
        """
        await self._ensure_db()
        if self._db is None:
            return None

        q = query.strip().lower()
        hits = [
            row
            for row in self._db
            if any(q in row.get(field, "").lower() for field in _SEARCH_FIELDS)
        ]

        if not hits:
            return AntibodyStructures(
                query=query,
                total_structures=0,
                nanobody_count=0,
                fab_count=0,
                structures=[],
            )

        structures = [_row_to_structure(r) for r in hits]

        # Sort: resolved entries (lower Å = better) first; unresolved last
        structures.sort(key=lambda s: (s.resolution_ang is None, s.resolution_ang or 99.0))

        nanobody_count = sum(1 for s in structures if s.is_nanobody)
        fab_count = len(structures) - nanobody_count

        return AntibodyStructures(
            query=query,
            total_structures=len(structures),
            nanobody_count=nanobody_count,
            fab_count=fab_count,
            structures=structures[:max_results],
        )

    # ------------------------------------------------------------------
    # Internal: cache management
    # ------------------------------------------------------------------

    async def _ensure_db(self) -> None:
        """Load (or refresh) the full SAbDab TSV into memory."""
        now = time.time()
        ttl = settings.sabdab_cache_ttl_secs

        # In-memory still fresh
        if self._db is not None and (now - self._db_loaded_at) < ttl:
            logger.debug("SAbDab: using in-memory cache (%d rows)", len(self._db))
            return

        # Try disk cache
        cache_path = settings.sabdab_cache_path
        if cache_path.exists():
            age = now - cache_path.stat().st_mtime
            if age < ttl:
                logger.debug("SAbDab: loading from disk cache at %s", cache_path)
                try:
                    raw = cache_path.read_bytes()
                    self._db = _parse_tsv(raw)
                    self._db_loaded_at = now
                    return
                except Exception as exc:
                    logger.warning("SAbDab: disk cache read failed: %s", exc)

        # Download fresh
        async with _SEMAPHORE:
            await self._download_and_cache(cache_path)

    async def _download_and_cache(self, cache_path) -> None:
        """Download the SAbDab summary TSV and persist to disk."""
        logger.info("SAbDab: downloading fresh summary TSV from %s", _SUMMARY_URL)
        try:
            resp = await self._client.get(_SUMMARY_URL, timeout=60.0)
            resp.raise_for_status()
            raw = resp.content
        except Exception as exc:
            logger.warning("SAbDab: download failed: %s", exc)
            # Keep stale cache if available
            if cache_path.exists():
                logger.warning("SAbDab: falling back to stale disk cache")
                try:
                    self._db = _parse_tsv(cache_path.read_bytes())
                    self._db_loaded_at = time.time()
                except Exception:
                    pass
            return

        # Persist to disk
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(raw)
        except Exception as exc:
            logger.warning("SAbDab: failed to write disk cache: %s", exc)

        self._db = _parse_tsv(raw)
        self._db_loaded_at = time.time()
        logger.info("SAbDab: loaded %d rows", len(self._db or []))
