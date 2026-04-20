"""Hardcoded indication → tissue mapping for expression-aware scoring.

Used by target prioritization to check whether the gene is expressed in the
tissue(s) where the indication manifests. The values are coarse canonical
tissue labels matched against GTEx/HPA tissue vocabulary (case-insensitive
substring match at lookup time).

This is an interim v0.3.0 solution. v0.3.1 plans to replace it with an
EFO → UBERON ontology-backed resolution so arbitrary indications can be
mapped without hand-curation.
"""

from __future__ import annotations

# Keys are lowercased canonical indication names. Values are lists of tissue
# tokens that appear in GTEx tissue labels (e.g. "Brain - Cortex") or HPA
# tissue names. Substring matching at query time — keep values short and
# unambiguous.
INDICATION_TISSUE_MAP: dict[str, list[str]] = {
    # Oncology
    "melanoma": ["skin"],
    "nsclc": ["lung"],
    "non-small cell lung cancer": ["lung"],
    "lung cancer": ["lung"],
    "breast cancer": ["breast"],
    "colorectal cancer": ["colon"],
    "pancreatic cancer": ["pancreas"],
    "prostate cancer": ["prostate"],
    "gastric cancer": ["stomach"],
    "hepatocellular carcinoma": ["liver"],
    "ovarian cancer": ["ovary"],
    "glioblastoma": ["brain"],
    "renal cell carcinoma": ["kidney"],
    "leukemia": ["blood", "bone marrow"],
    "lymphoma": ["lymph node", "blood"],
    "multiple myeloma": ["bone marrow"],
    # Cardiovascular / metabolic
    "hypercholesterolemia": ["liver"],
    "atherosclerosis": ["artery", "blood vessel"],
    "heart failure": ["heart"],
    "type 2 diabetes": ["pancreas", "liver", "muscle"],
    "nash": ["liver"],
    "nafld": ["liver"],
    "obesity": ["adipose"],
    # Immunology / inflammation
    "rheumatoid arthritis": ["synovium", "joint"],
    "psoriasis": ["skin"],
    "inflammatory bowel disease": ["colon", "intestine"],
    "ibd": ["colon", "intestine"],
    "crohn's disease": ["intestine"],
    "ulcerative colitis": ["colon"],
    "ra": ["synovium", "joint"],
    # Neurology
    "alzheimer's disease": ["brain"],
    "parkinson's disease": ["brain"],
    "als": ["spinal cord", "brain"],
    "multiple sclerosis": ["brain", "spinal cord"],
    # Rare / other
    "cystic fibrosis": ["lung"],
    "sickle cell disease": ["blood"],
}


def tissues_for_indication(indication: str) -> list[str]:
    """Return the mapped tissue tokens for ``indication``, or empty list."""
    key = (indication or "").strip().lower()
    if not key:
        return []
    if key in INDICATION_TISSUE_MAP:
        return list(INDICATION_TISSUE_MAP[key])
    # Relaxed substring fallback: many indications arrive with suffixes
    # like "(metastatic)" or prefixes like "advanced".
    for canonical, tissues in INDICATION_TISSUE_MAP.items():
        if canonical in key or key in canonical:
            return list(tissues)
    return []
