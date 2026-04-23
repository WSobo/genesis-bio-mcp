"""OpenFDA client — post-market drug safety signals (FAERS + label + recalls).

Wraps three OpenFDA endpoints:

- ``GET /drug/event.json``       — FAERS spontaneous adverse event reports
- ``GET /drug/label.json``       — structured product label (incl. boxed warnings)
- ``GET /drug/enforcement.json`` — recalls / enforcement actions

No API key is required for light use (~240 req/min, 1000 req/day per IP).
If ``OPENFDA_API_KEY`` is set in the environment, it is appended as
``?api_key=...`` to raise those quotas substantially.

FAERS reports are voluntary and unverified; counts do not imply causation.
The :class:`DrugSafetySignal` model carries a ``disclaimer`` field so downstream
markdown renderings always surface that caveat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

from genesis_bio_mcp.config.settings import settings
from genesis_bio_mcp.models import AdverseEventCount, DrugRecall, DrugSafetySignal

logger = logging.getLogger(__name__)

_OPENFDA_BASE = "https://api.fda.gov"
_SEMAPHORE = asyncio.Semaphore(2)

# Cap the number of reaction terms we request from FAERS. OpenFDA returns the
# full count-by-field distribution; we only render the top few, and truncating
# upstream keeps the disk cache small.
_REACTION_LIMIT = 10
_RECALL_LIMIT = 5


class OpenFDAClient:
    """Session + disk-cached OpenFDA client.

    Returns ``None`` only when *all three* sub-queries fail for a drug.
    A drug that simply has no FAERS reports, no boxed warning, and no recalls
    yields a populated but empty :class:`DrugSafetySignal` (not ``None``) so
    callers can distinguish "clean record" from "lookup failed."
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._api_key = os.environ.get("OPENFDA_API_KEY")
        self._session_cache: dict[str, DrugSafetySignal] = {}
        self._disk_cache_path: Path = settings.openfda_cache_path
        self._disk_cache: dict[str, dict] = _load_disk_cache(self._disk_cache_path)

    async def get_safety_signals(self, drug_name: str) -> DrugSafetySignal | None:
        """Return a :class:`DrugSafetySignal` for *drug_name*.

        The lookup matches either generic or brand name; OpenFDA indexes both.
        """
        key = drug_name.strip().lower()
        if not key:
            return None

        if key in self._session_cache:
            logger.debug("OpenFDA session cache hit: %s", key)
            return self._session_cache[key]

        disk_entry = self._disk_cache.get(key)
        if (
            disk_entry
            and time.time() - disk_entry.get("fetched_at", 0) < settings.openfda_cache_ttl_secs
        ):
            try:
                signal = DrugSafetySignal(**disk_entry["signal"])
                self._session_cache[key] = signal
                return signal
            except Exception as exc:
                logger.debug("OpenFDA disk cache entry for %s stale: %s", key, exc)

        async with _SEMAPHORE:
            events, label, recalls = await asyncio.gather(
                self._fetch_faers_reactions(drug_name),
                self._fetch_label_warnings(drug_name),
                self._fetch_recalls(drug_name),
            )

        # If every sub-query returned None (network-level failure rather than
        # empty-result), surface that to the caller as None so they can retry.
        if events is None and label is None and recalls is None:
            return None

        top_aes, total_reports = events or ([], 0)
        signal = DrugSafetySignal(
            drug_name=drug_name,
            total_reports=total_reports,
            top_adverse_events=top_aes,
            boxed_warnings=label or [],
            recalls=recalls or [],
        )
        self._session_cache[key] = signal
        # Only persist non-empty signals — if a drug currently has no FAERS
        # data but gets reports tomorrow, we want to pick those up.
        if signal.total_reports or signal.boxed_warnings or signal.recalls:
            self._disk_cache[key] = {
                "fetched_at": time.time(),
                "signal": signal.model_dump(),
            }
            _save_disk_cache(self._disk_cache_path, self._disk_cache)
        return signal

    # -----------------------------------------------------------------------
    # Sub-queries
    # -----------------------------------------------------------------------

    async def _fetch_faers_reactions(
        self, drug_name: str
    ) -> tuple[list[AdverseEventCount], int] | None:
        """Return (top reactions, total-reports) or ``None`` on error.

        OpenFDA's count-mode response omits the raw report total, so we issue
        a second lightweight search-only call to recover it. That's still
        cheap — both responses are under 2 kB.
        """
        quoted = _quote(drug_name)
        # Search both brand and generic names via boolean OR so we don't miss
        # either match pattern. OpenFDA's Lucene-style query supports this.
        search = (
            f"(patient.drug.medicinalproduct:{quoted}+"
            f"patient.drug.openfda.generic_name:{quoted}+"
            f"patient.drug.openfda.brand_name:{quoted})"
        )
        url = f"{_OPENFDA_BASE}/drug/event.json"
        count_params = {
            "search": search,
            "count": "patient.reaction.reactionmeddrapt.exact",
            "limit": _REACTION_LIMIT,
        }
        total_params = {"search": search, "limit": 1}

        try:
            count_resp, total_resp = await asyncio.gather(
                self._get(url, count_params),
                self._get(url, total_params),
            )
        except Exception as exc:
            logger.warning("OpenFDA FAERS fetch failed for %s: %s", drug_name, exc)
            return None

        if count_resp is None and total_resp is None:
            return None
        # 404 from OpenFDA means "no matching records" — that's not an error,
        # it's a clean negative result.
        count_data = count_resp or {}
        total_data = total_resp or {}

        top_aes: list[AdverseEventCount] = []
        for row in count_data.get("results", [])[:_REACTION_LIMIT]:
            term = (row.get("term") or "").strip()
            count = row.get("count")
            if term and isinstance(count, int) and count > 0:
                top_aes.append(AdverseEventCount(term=term, count=count))

        total = total_data.get("meta", {}).get("results", {}).get("total", 0) or 0
        return top_aes, int(total)

    async def _fetch_label_warnings(self, drug_name: str) -> list[str] | None:
        """Return the label's boxed warnings (list of strings) or ``None``."""
        quoted = _quote(drug_name)
        search = f"(openfda.generic_name:{quoted}+openfda.brand_name:{quoted})"
        url = f"{_OPENFDA_BASE}/drug/label.json"
        params = {"search": search, "limit": 1}

        data = await self._get(url, params)
        if data is None:
            return None

        results = data.get("results") or []
        if not results:
            return []

        warnings: list[str] = []
        for entry in results[:1]:
            # The label API returns ``boxed_warning`` as a list of strings.
            # Some older labels stash it under ``warnings`` instead; ignore
            # those (too noisy — warnings are long-form narrative).
            raw = entry.get("boxed_warning") or []
            if isinstance(raw, str):
                raw = [raw]
            for text in raw:
                text = (text or "").strip()
                if text:
                    warnings.append(text)
        return warnings

    async def _fetch_recalls(self, drug_name: str) -> list[DrugRecall] | None:
        """Return active/historical recalls for the product, or ``None``."""
        quoted = _quote(drug_name)
        search = f"(openfda.generic_name:{quoted}+openfda.brand_name:{quoted})"
        url = f"{_OPENFDA_BASE}/drug/enforcement.json"
        params = {"search": search, "limit": _RECALL_LIMIT}

        data = await self._get(url, params)
        if data is None:
            return None

        recalls: list[DrugRecall] = []
        for row in (data.get("results") or [])[:_RECALL_LIMIT]:
            rnum = (row.get("recall_number") or "").strip()
            reason = (row.get("reason_for_recall") or "").strip()
            if not rnum or not reason:
                continue
            recalls.append(
                DrugRecall(
                    recall_number=rnum,
                    classification=(row.get("classification") or None),
                    reason=reason,
                    status=(row.get("status") or None),
                )
            )
        return recalls

    async def _get(self, url: str, params: dict) -> dict | None:
        """GET with API-key injection. 404 returns ``{}``; errors return ``None``."""
        merged = dict(params)
        if self._api_key:
            merged["api_key"] = self._api_key
        try:
            resp = await self._client.get(url, params=merged, timeout=20.0)
        except Exception as exc:
            logger.warning("OpenFDA GET %s failed: %s", url, exc)
            return None
        # 404 from OpenFDA = zero matches; treat as empty, not an error.
        if resp.status_code == 404:
            return {}
        if resp.status_code >= 400:
            logger.warning(
                "OpenFDA GET %s returned %s: %s",
                url,
                resp.status_code,
                resp.text[:200],
            )
            return None
        try:
            return resp.json()
        except Exception as exc:
            logger.warning("OpenFDA GET %s: invalid JSON: %s", url, exc)
            return None


def _quote(value: str) -> str:
    """OpenFDA accepts quoted values for multi-word / cased matches."""
    cleaned = value.strip().replace('"', "")
    return f'"{cleaned}"'


def _load_disk_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        logger.warning("OpenFDA disk cache unreadable at %s: %s", path, exc)
        return {}


def _save_disk_cache(path: Path, cache: dict[str, dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache))
    except Exception as exc:
        logger.warning("OpenFDA disk cache write failed at %s: %s", path, exc)
