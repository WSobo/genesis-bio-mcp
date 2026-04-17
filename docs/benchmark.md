# Benchmark

End-to-end `prioritize_target` results against live APIs — 12
representative targets spanning oncology, metabolic disease, autoimmune,
and pain biology (single session, warm DepMap/EFO cache).

For the scoring model itself see
[architecture.md#scoring-model--prioritize_target](architecture.md#scoring-model--prioritize_target).

| Gene input | Resolved | Indication | Score | Tier | GWAS | Time |
|---|---|---|---|---|---|---|
| BRAF | BRAF | melanoma | 7.2 | High | — somatic driver | 4.3s |
| EGFR | EGFR | non-small cell lung carcinoma | 7.5 | High | — | 2.2s |
| KRAS | KRAS | pancreatic cancer | 4.9 | Medium | — | 2.2s |
| **HER2** | **ERBB2** | breast cancer | 7.6 | High | — | 2.0s |
| PCSK9 | PCSK9 | hypercholesterolemia | **9.1** | High | ✓ 4 hits, p=4×10⁻²⁰ | 2.3s |
| FTO | FTO | obesity | 4.3 | Medium | timeout† | 14.4s |
| TNF | TNF | rheumatoid arthritis | **7.8** | High | ✓ 1 hit, p=9×10⁻²⁵ | 9.1s |
| PTGS2 | PTGS2 | inflammation | 7.7 | High | — | 7.2s |
| TP53 | TP53 | squamous cell carcinoma | 3.6 | Low | — | 3.8s |
| CD274 | CD274 | melanoma | 4.1 | Medium | — | 6.3s |
| **p53** | **TP53** | lung cancer | 3.4 | Low | — session cache | 3.3s |
| **COX2** | **PTGS2** | pain | **7.8** | High | — session cache | **1.9s** |

**Bold** gene inputs indicate alias resolution. GWAS gaps for BRAF / EGFR
/ KRAS are biologically expected — somatic cancer drivers are not GWAS
loci. COX2/pain at 1.9s demonstrates the session gene cache: PTGS2
associations were already fetched for the inflammation query and are
reused instantly.

†FTO has extensive GWAS signal (strong obesity associations with p < 10⁻¹⁰⁰)
but the Catalog API is slow for high-association genes; the 24h disk
cache rescues repeat queries.

---

## Example output — `prioritize_target("BRAF", "melanoma")`

### Standard mode

```
→ priority_score: 7.2 / 10
→ priority_tier: High
→ evidence_summary: "BRAF shows strong Open Targets association with melanoma
  (score: 0.82, n=5 evidence items). Open Targets reports strong known-drug
  evidence (score: 0.98), suggesting existing approved therapeutics. DepMap
  CRISPR data show dependency in 9% of cancer lines, highest in differentiated
  thyroid carcinoma, glioblastoma multiforme, lung adenocarcinoma. ChEMBL
  reports 68 compounds; best IC50 ≈ 0.3 nM (pChEMBL=9.5)."

→ disease_association.overall_score:          0.82
→ disease_association.somatic_mutation_score: 0.80   # BRAF V600E is the canonical somatic driver
→ disease_association.known_drug_score:       0.98   # vemurafenib, dabrafenib, encorafenib
→ cancer_dependency.fraction_dependent_lines: 0.09   # 9% — lineage-selective (melanoma)
→ chembl_compounds.best_pchembl:              9.5    # clinical-grade potency
→ chembl_compounds.total_active_compounds:    68
→ data_gaps: ["gwas"]                                # expected — somatic, not germline
```

### Confidence assessment

```
→ data_coverage_pct:         83.3   # 5 of 6 core sources returned data
→ score_confidence_interval: (6.0, 8.2)
→ proxy_data_flags:          {}     # all real data, no OT proxies used
```

### Extended mode (`extended=True`)

Passes the same target through all four lab-loop tools in one parallel
gather:

```
→ protein_structure.alphafold_plddt:    92.1       # high confidence (≥90)
→ protein_structure.best_resolution:    1.7 Å
→ protein_structure.has_ligand_bound:   true       # inhibitor co-crystal available
→ protein_interactome.top_partners:    MAP2K1 (0.999), MAP2K2 (0.998), RAF1 (0.963)
→ drug_history.approved_drug_count:    4
→ drug_history.trial_counts_by_phase:  {"Phase 1": 12, "Phase 2": 8, "Phase 3": 3}
→ pathway_context.top_pathway:         "MAPK1/MAPK2 Cascade" (p=2.3e-15)
```
