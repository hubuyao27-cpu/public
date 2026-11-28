#!/usr/bin/env python3
"""Self-contained regression & adversarial test suite for literature-kg-builder Skill.

Runs end-to-end on examples/synthetic_corpus/ inside a temporary directory (zero side effects),
validates all positive invariants, and executes 7 adversarial negative tests to prove
verify_kg.py mechanically blocks every rubric violation.

Pure Python 3.10+ standard library.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
SYNTH_CORPUS = SKILL_ROOT / "examples" / "synthetic_corpus"


def run_cmd(args: List[str], expect_rc: int = 0) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable] + args, capture_output=True, text=True, check=False)
    if proc.returncode != expect_rc:
        raise AssertionError(
            f"Command {args} returned {proc.returncode} (expected {expect_rc}).\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def build_synthetic_extractions() -> Dict[str, Any]:
    primary_extraction = {
        "source_id": "paper_primary",
        "entities": [
            {
                "temp_id": "P1_E1",
                "canonical_name": "HP-Ni-N-C (paper_primary)",
                "entity_type": "Material",
                "aliases": ["hollow porous Ni-N-C catalyst", "HP-Ni-N-C", "Ni-N-C"],
                "attributes": {"pyrolysis_temp": "900 C", "reactor": "H-cell", "electrolyte": "0.5 M KHCO3"},
                "mentions": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-abs-1",
                        "section": "Abstract",
                        "surface_form": "hollow porous Ni-N-C catalyst (HP-Ni-N-C)",
                        "quote": "We synthesized a hollow porous Ni-N-C catalyst (HP-Ni-N-C) via pyrolysis at 900 C",
                        "attribution": "author_method_statement",
                    }
                ],
            },
            {
                "temp_id": "P1_E2",
                "canonical_name": "CO2 electroreduction",
                "entity_type": "Task",
                "aliases": ["CO2RR"],
                "mentions": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-abs-1",
                        "section": "Abstract",
                        "surface_form": "CO2 electroreduction (CO2RR)",
                        "quote": "for CO2 electroreduction (CO2RR) in an H-cell with 0.5 M KHCO3 electrolyte.",
                        "attribution": "author_method_statement",
                    }
                ],
            },
            {
                "temp_id": "P1_E3",
                "canonical_name": "Online mass spectrometry (MS)",
                "entity_type": "Method",
                "aliases": ["MS", "mass spectrometry"],
                "mentions": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-meth-1",
                        "section": "Methods",
                        "surface_form": "Online mass spectrometry (MS)",
                        "quote": "Online mass spectrometry (MS) and gas chromatography were applied to quantify CO product selectivity",
                        "attribution": "author_method_statement",
                    }
                ],
            },
            {
                "temp_id": "P1_E4",
                "canonical_name": "Faradaic efficiency",
                "entity_type": "Metric",
                "aliases": ["FE"],
                "mentions": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-meth-1",
                        "section": "Methods",
                        "surface_form": "Faradaic efficiency (FE)",
                        "quote": "quantify CO product selectivity and Faradaic efficiency (FE) across applied potentials.",
                        "attribution": "author_method_statement",
                    }
                ],
            },
            {
                "temp_id": "P1_E5",
                "canonical_name": "40 h stable H-cell electrolysis without flooding (paper_primary)",
                "entity_type": "Conclusion",
                "aliases": ["40 h stability"],
                "mentions": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-res-1",
                        "section": "Results",
                        "surface_form": "stable electrolysis for 40 h without noticeable flooding",
                        "quote": "maintained stable electrolysis for 40 h without noticeable flooding.",
                        "attribution": "author_direct_result",
                    }
                ],
            },
            {
                "temp_id": "P1_E6",
                "canonical_name": "Cu-N-C baseline (Smith et al.)",
                "entity_type": "Material",
                "aliases": ["Cu-N-C"],
                "mentions": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-disc-1",
                        "section": "Discussion",
                        "surface_form": "Cu-N-C baseline",
                        "quote": "Smith et al. reported that a Cu-N-C baseline reached 78.0% FE for CO under bicarbonate conditions",
                        "attribution": "cited_prior_work",
                    }
                ],
            },
        ],
        "relations": [
            {
                "source": "P1_E1",
                "target": "P1_E2",
                "relation_type": "APPLIED_TO",
                "attribution": "author_method_statement",
                "evidence_strength": "strong_direct",
                "conditions": "Pyrolyzed at 900 C; H-cell; 0.5 M KHCO3 electrolyte",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-abs-1",
                        "quote": "hollow porous Ni-N-C catalyst (HP-Ni-N-C) via pyrolysis at 900 C for CO2 electroreduction (CO2RR) in an H-cell with 0.5 M KHCO3 electrolyte.",
                    }
                ],
            },
            {
                "source": "P1_E3",
                "target": "P1_E4",
                "relation_type": "MEASURES",
                "attribution": "author_method_statement",
                "evidence_strength": "strong_direct",
                "conditions": "Combined with gas chromatography across applied potentials",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-meth-1",
                        "quote": "Online mass spectrometry (MS) and gas chromatography were applied to quantify CO product selectivity and Faradaic efficiency (FE)",
                    }
                ],
            },
            {
                "source": "P1_E1",
                "target": "P1_E4",
                "relation_type": "REPORTS_METRIC",
                "attribution": "author_direct_result",
                "evidence_strength": "strong_direct",
                "conditions": "FE_CO = 91.5% at -0.8 V vs RHE in 0.5 M KHCO3 H-cell",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-res-1",
                        "quote": "HP-Ni-N-C achieved a Faradaic efficiency of 91.5% for CO at -0.8 V vs RHE in 0.5 M KHCO3",
                    }
                ],
            },
            {
                "source": "P1_E1",
                "target": "P1_E5",
                "relation_type": "SUPPORTS",
                "attribution": "author_direct_result",
                "evidence_strength": "strong_direct",
                "conditions": "H-cell, 0.5 M KHCO3, -0.8 V vs RHE",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-res-1",
                        "quote": "HP-Ni-N-C achieved a Faradaic efficiency of 91.5% for CO at -0.8 V vs RHE in 0.5 M KHCO3 and maintained stable electrolysis for 40 h without noticeable flooding.",
                    }
                ],
            },
            {
                "source": "P1_E6",
                "target": "P1_E4",
                "relation_type": "CITES_PRIOR_CLAIM",
                "attribution": "cited_prior_work",
                "evidence_strength": "cited_secondary",
                "conditions": "78.0% FE for CO under bicarbonate conditions (cited from Smith et al.)",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-disc-1",
                        "quote": "Smith et al. reported that a Cu-N-C baseline reached 78.0% FE for CO under bicarbonate conditions",
                    }
                ],
            },
        ],
    }

    secondary_extraction = {
        "source_id": "paper_secondary",
        "entities": [
            {
                "temp_id": "P2_E1",
                "canonical_name": "P-Ni-N-C (paper_secondary)",
                "entity_type": "Material",
                "aliases": ["planar Ni-N-C catalyst", "P-Ni-N-C", "Ni-N-C"],
                "attributes": {
                    "pyrolysis_temp": "800 C",
                    "reactor": "gas-diffusion flow cell",
                    "electrolyte": "1.0 M KOH",
                    "xml_verified_current_density": "200 mA cm^{-2}",
                },
                "mentions": [
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-abs-1",
                        "section": "Abstract",
                        "surface_form": "planar Ni-N-C catalyst (P-Ni-N-C)",
                        "quote": "A planar Ni-N-C catalyst (P-Ni-N-C) pyrolyzed at 800 C was evaluated for CO2RR",
                        "attribution": "author_method_statement",
                    }
                ],
            },
            {
                "temp_id": "P2_E2",
                "canonical_name": "CO2RR",
                "entity_type": "Task",
                "aliases": ["CO2RR"],
                "mentions": [
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-abs-1",
                        "section": "Abstract",
                        "surface_form": "CO2RR",
                        "quote": "evaluated for CO2RR in a gas-diffusion flow cell using 1.0 M KOH electrolyte.",
                        "attribution": "author_method_statement",
                        "xml_verified_formula": "CO_{2}RR",
                    }
                ],
            },
            {
                "temp_id": "P2_E3",
                "canonical_name": "Anion-exchange membrane separator (MS)",
                "entity_type": "Material",
                "aliases": ["MS", "membrane separator"],
                "attributes": {"flow_rate_xml_verified": "10^{-3} m^{3}/h"},
                "mentions": [
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-meth-1",
                        "section": "Methods",
                        "surface_form": "anion-exchange membrane separator (MS)",
                        "quote": "An anion-exchange membrane separator (MS) separated the catholyte and anolyte chambers at a flow rate of 10-3 m3/h.",
                        "attribution": "author_method_statement",
                        "xml_verified_formula": "10^{-3} m^{3}/h",
                    }
                ],
            },
            {
                "temp_id": "P2_E4",
                "canonical_name": "FE",
                "entity_type": "Metric",
                "aliases": ["FE"],
                "mentions": [
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-res-1",
                        "section": "Results",
                        "surface_form": "FE",
                        "quote": "P-Ni-N-C delivered an initial FE of 94.2% at -0.6 V vs RHE and 200 mA cm-2",
                        "attribution": "author_direct_result",
                        "xml_verified_formula": "FE_{\\mathrm{CO}} = 94.2\\%",
                    }
                ],
            },
            {
                "temp_id": "P2_E5",
                "canonical_name": "Commercial Ag nanoparticles",
                "entity_type": "Material",
                "aliases": ["Ag nanoparticles", "Commercial Ag NPs"],
                "mentions": [
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-res-1",
                        "section": "Results",
                        "surface_form": "commercial Ag nanoparticles",
                        "quote": "outperforming commercial Ag nanoparticles (83.1% FE) under identical 1.0 M KOH flow-cell conditions",
                        "attribution": "author_direct_result",
                    }
                ],
            },
            {
                "temp_id": "P2_E6",
                "canonical_name": "Electrolyte flooding after 12 h in alkaline flow cell (paper_secondary)",
                "entity_type": "Conclusion",
                "aliases": ["flooding after 12 h"],
                "mentions": [
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-res-1",
                        "section": "Results",
                        "surface_form": "suffered electrolyte flooding after 12 h",
                        "quote": "but suffered electrolyte flooding after 12 h (see Figure S2 for raw impedance curves).",
                        "attribution": "author_direct_result",
                    }
                ],
            },
        ],
        "relations": [
            {
                "source": "P2_E1",
                "target": "P2_E2",
                "relation_type": "APPLIED_TO",
                "attribution": "author_method_statement",
                "evidence_strength": "strong_direct",
                "conditions": "Pyrolyzed at 800 C; gas-diffusion flow cell; 1.0 M KOH",
                "evidence": [
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-abs-1",
                        "quote": "A planar Ni-N-C catalyst (P-Ni-N-C) pyrolyzed at 800 C was evaluated for CO2RR in a gas-diffusion flow cell using 1.0 M KOH electrolyte.",
                    }
                ],
            },
            {
                "source": "P2_E1",
                "target": "P2_E4",
                "relation_type": "REPORTS_METRIC",
                "attribution": "author_direct_result",
                "evidence_strength": "strong_direct",
                "conditions": "Initial FE_CO = 94.2% at -0.6 V vs RHE, 200 mA cm^{-2} (XML verified), 1.0 M KOH flow cell",
                "evidence": [
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-res-1",
                        "quote": "P-Ni-N-C delivered an initial FE of 94.2% at -0.6 V vs RHE and 200 mA cm-2",
                    }
                ],
            },
            {
                "source": "P2_E1",
                "target": "P2_E5",
                "relation_type": "OUTPERFORMS",
                "attribution": "author_direct_result",
                "evidence_strength": "strong_direct",
                "conditions": "94.2% FE vs 83.1% FE at -0.6 V vs RHE, 200 mA cm^{-2}, identical 1.0 M KOH flow-cell conditions",
                "evidence": [
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-res-1",
                        "quote": "outperforming commercial Ag nanoparticles (83.1% FE) under identical 1.0 M KOH flow-cell conditions",
                    }
                ],
            },
            {
                "source": "P2_E1",
                "target": "P2_E6",
                "relation_type": "SUPPORTS",
                "attribution": "author_direct_result",
                "evidence_strength": "strong_direct",
                "conditions": "1.0 M KOH flow cell, 200 mA cm^{-2}",
                "evidence": [
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-res-1",
                        "quote": "but suffered electrolyte flooding after 12 h (see Figure S2 for raw impedance curves).",
                    }
                ],
            },
        ],
    }

    alignment_decisions = {
        "decisions": [
            {
                "decision_id": "A001",
                "candidate_a": {"node_id_or_name": "E002", "source_id": "paper_primary"},
                "candidate_b": {"node_id_or_name": "P2_E2", "source_id": "paper_secondary"},
                "verdict": "merge_alias",
                "canonical_target_id": "E002",
                "rationale": "Paper 1 explicitly defines 'CO2 electroreduction (CO2RR)' and Paper 2 uses the same standard electrochemical reaction task 'CO2RR' (XML: CO_{2}RR).",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-abs-1",
                        "quote": "CO2 electroreduction (CO2RR)",
                    },
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-abs-1",
                        "quote": "evaluated for CO2RR",
                    },
                ],
            },
            {
                "decision_id": "A002",
                "candidate_a": {"node_id_or_name": "E004", "source_id": "paper_primary"},
                "candidate_b": {"node_id_or_name": "P2_E4", "source_id": "paper_secondary"},
                "verdict": "merge_alias",
                "canonical_target_id": "E004",
                "rationale": "Paper 1 defines 'Faradaic efficiency (FE)' and Paper 2 reports the same standard metric 'FE' (XML: FE_CO). Numeric results remain on separate paper-scoped edges.",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-meth-1",
                        "quote": "Faradaic efficiency (FE)",
                    },
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-res-1",
                        "quote": "initial FE of 94.2%",
                    },
                ],
            },
            {
                "decision_id": "A003",
                "candidate_a": {"node_id_or_name": "E001", "source_id": "paper_primary"},
                "candidate_b": {"node_id_or_name": "P2_E1", "source_id": "paper_secondary"},
                "verdict": "keep_separate_distinct_instance",
                "canonical_target_id": None,
                "shared_concept_name": "Ni-N-C single-atom catalyst family",
                "rationale": "DO NOT MERGE: Although both papers share the generic material term 'Ni-N-C', Paper 1 tests a hollow porous catalyst pyrolyzed at 900 C in 0.5 M KHCO3 H-cell (91.5% FE, 40 h), whereas Paper 2 tests a planar catalyst pyrolyzed at 800 C in 1.0 M KOH flow cell (94.2% FE, 12 h flooding). Link via abstract Concept only.",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-abs-1",
                        "quote": "hollow porous Ni-N-C catalyst (HP-Ni-N-C) via pyrolysis at 900 C",
                    },
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-abs-1",
                        "quote": "planar Ni-N-C catalyst (P-Ni-N-C) pyrolyzed at 800 C",
                    },
                ],
            },
            {
                "decision_id": "A004",
                "candidate_a": {"node_id_or_name": "E003", "source_id": "paper_primary"},
                "candidate_b": {"node_id_or_name": "P2_E3", "source_id": "paper_secondary"},
                "verdict": "keep_separate_homonym",
                "canonical_target_id": None,
                "rationale": "DO NOT MERGE HOMONYM: 'MS' denotes 'Online mass spectrometry' (analytical characterization method) in Paper 1, but denotes 'anion-exchange membrane separator' (reactor hardware component) in Paper 2.",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-meth-1",
                        "quote": "Online mass spectrometry (MS)",
                    },
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-meth-1",
                        "quote": "anion-exchange membrane separator (MS)",
                    },
                ],
            },
        ]
    }

    cross_relations = {
        "relations": [
            {
                "source": "E005",
                "target": "P2_E6",
                "relation_type": "CONTRADICTS",
                "attribution": "author_direct_result",
                "evidence_strength": "moderate_indirect",
                "conditions": "Different reactor & electrolyte conditions: Paper 1 reports 40 h stability without flooding (HP-Ni-N-C, H-cell, 0.5 M KHCO3) vs Paper 2 flooding after 12 h (P-Ni-N-C, flow cell, 1.0 M KOH, 200 mA cm^{-2}); stability outcomes cannot be collapsed.",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-res-1",
                        "quote": "maintained stable electrolysis for 40 h without noticeable flooding.",
                    },
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-res-1",
                        "quote": "suffered electrolyte flooding after 12 h",
                    },
                ],
            }
        ],
        "conflicts": [
            {
                "conflict_id": "CF001",
                "conflict_type": "cross_paper_condition_mismatch",
                "description": "Divergent stability outcomes for Ni-N-C catalysts across papers (40 h non-flooding in H-cell 0.5 M KHCO3 vs 12 h flooding in 1.0 M KOH flow cell at 200 mA cm^{-2}) reflect distinct physical samples and test conditions; preserved separately without merging.",
                "evidence": [
                    {
                        "source_id": "paper_primary",
                        "paragraph_id": "p1-res-1",
                        "quote": "maintained stable electrolysis for 40 h without noticeable flooding.",
                    },
                    {
                        "source_id": "paper_secondary",
                        "paragraph_id": "p2-res-1",
                        "quote": "suffered electrolyte flooding after 12 h",
                    },
                ],
            }
        ],
    }

    query_specs = {
        "queries": [
            {
                "query_id": "Q1",
                "question": "Compare the Ni-N-C catalysts, conditions, and metrics in Paper 1 and Paper 2 without merging distinct physical samples.",
                "operator": "cross_paper_comparison",
                "params": {"concept_or_term": "Ni-N-C single-atom catalyst family"},
            },
            {
                "query_id": "Q2",
                "question": "Trace verifiable direct author evidence chains from Materials/Methods to Metrics and Conclusions.",
                "operator": "multi_hop_chain",
                "params": {
                    "start_types": ["Material", "Method"],
                    "end_types": ["Metric", "Conclusion"],
                    "max_hops": 3,
                    "exclude_attributions": ["cited_prior_work", "speculation_or_hypothesis"],
                },
            },
            {
                "query_id": "Q3",
                "question": "Audit all cross-paper conflicts, non-merge boundaries (distinct samples & homonyms), and cited prior claims.",
                "operator": "conflict_and_boundary_audit",
                "params": {"include_non_merges": True, "include_cited_or_speculative": True},
            },
            {
                "query_id": "Q4",
                "question": "Is there direct controlled experimental evidence comparing HP-Ni-N-C and P-Ni-N-C head-to-head in the same flow cell, or raw Figure S2 impedance values?",
                "operator": "gap_or_unanswerable_check",
                "params": {
                    "entity_a": "HP-Ni-N-C (paper_primary)",
                    "entity_b": "P-Ni-N-C (paper_secondary)",
                    "required_relation_types": ["OUTPERFORMS", "COMPARES"],
                    "require_direct_author_evidence": True,
                    "missing_modality_check": ["figure_pixels_unavailable", "supplementary_unavailable"],
                },
            },
        ]
    }
    return {
        "primary": primary_extraction,
        "secondary": secondary_extraction,
        "alignment": alignment_decisions,
        "cross": cross_relations,
        "queries": query_specs,
    }


def run_negative_tests(
    work: Path,
    kg_v1: Dict[str, Any],
    kg_v2: Dict[str, Any],
    corpus_index_path: Path,
    xml_recon_path: Path,
    queries_path: Path,
) -> int:
    """Run 7 adversarial negative mutations and assert verify_kg.py rejects every single one."""
    neg_cases = []

    # 1. Fabricated quote
    k1 = copy.deepcopy(kg_v2)
    k1["edges"][0]["evidence"][0]["quote"] = "Fabricated hallucinated sentence never written in any paper"
    neg_cases.append(("fabricated_quote", k1, "quote_not_grounded_in_paragraph", None))

    # 2. Fake PDF page 1 attribution when has_pdf_pages == false
    k2 = copy.deepcopy(kg_v2)
    k2["nodes"][0]["mentions"][0]["locator_type"] = "pdf_page"
    k2["nodes"][0]["mentions"][0]["page"] = 1
    neg_cases.append(("fake_pdf_page", k2, "fake_pdf_page_attribution", None))

    # 3. Root primary DOI leaked onto secondary paper
    k3 = copy.deepcopy(kg_v2)
    for d in k3["documents"]:
        if d["source_id"] == "paper_secondary":
            d["doi"] = "10.9999/synth.primary.2025.001"
    neg_cases.append(("root_metadata_bleed", k3, "root_metadata_bleed_to_secondary", None))

    # 4. Same-paper multi-format claimed as cross-paper edge
    k4 = copy.deepcopy(kg_v2)
    k4["edges"][0]["is_cross_paper"] = True
    neg_cases.append(("same_paper_claimed_cross_paper", k4, "same_paper_format_claimed_cross_paper", None))

    # 5. Cited prior work mislabeled as strong_direct
    k5 = copy.deepcopy(kg_v2)
    for e in k5["edges"]:
        if e.get("attribution") == "cited_prior_work":
            e["evidence_strength"] = "strong_direct"
            break
    neg_cases.append(("cited_as_strong_direct", k5, "epistemic_strength_violation", None))

    # 6. OUTPERFORMS missing conditions
    k6 = copy.deepcopy(kg_v2)
    for e in k6["edges"]:
        if e.get("relation_type") == "OUTPERFORMS":
            e["conditions"] = ""
            break
    neg_cases.append(("outperforms_missing_conditions", k6, "missing_relation_conditions", None))

    # 7. Incremental update dropped/renumbered a v1 node ID
    k7 = copy.deepcopy(kg_v2)
    k7["nodes"] = [n for n in k7["nodes"] if n["id"] != "E006"]
    k7["edges"] = [e for e in k7["edges"] if e["source"] != "E006" and e["target"] != "E006"]
    neg_cases.append(("incremental_v1_id_lost", k7, "incremental_node_id_lost", kg_v1))

    passed = 0
    for name, bad_kg, expected_code, prev in neg_cases:
        bad_path = work / f"neg_{name}.json"
        rep_path = work / f"neg_{name}_report.json"
        bad_path.write_text(json.dumps(bad_kg, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd = [
            str(SCRIPT_DIR / "verify_kg.py"),
            "--kg",
            str(bad_path),
            "--corpus-index",
            str(corpus_index_path),
            "--xml-recon",
            str(xml_recon_path),
            "--queries",
            str(queries_path),
            "--out",
            str(rep_path),
        ]
        if prev is not None:
            prev_p = work / "neg_prev_v1.json"
            prev_p.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
            cmd.extend(["--prev-kg", str(prev_p)])
        run_cmd(cmd, expect_rc=1)
        rep = json.loads(rep_path.read_text(encoding="utf-8"))
        codes = {i["code"] for i in rep.get("issues", [])}
        if expected_code not in codes:
            raise AssertionError(f"Negative test '{name}' failed to emit '{expected_code}'. Got: {sorted(codes)}")
        passed += 1
    return passed


def main() -> None:
    if not SYNTH_CORPUS.exists():
        raise SystemExit(f"Missing synthetic corpus directory: {SYNTH_CORPUS}")

    with tempfile.TemporaryDirectory(prefix="kg_skill_selftest_") as td:
        tmp = Path(td)
        corpus_copy = tmp / "synth_corpus"
        shutil.copytree(SYNTH_CORPUS, corpus_copy)
        xml_md = corpus_copy / "secondary-publisher-xml.md"
        if xml_md.exists():
            m_xml = re.search(r"```xml\s*(.*?)\s*```", xml_md.read_text(encoding="utf-8"), re.S)
            if m_xml:
                (corpus_copy / "secondary-publisher.xml").write_text(m_xml.group(1) + "\n", encoding="utf-8")
        inspect_dir = tmp / "inspect"
        work_dir = tmp / "work"
        out_dir = tmp / "output"
        work_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Inspect synthetic 2-paper JATS + XML corpus
        run_cmd(
            [
                str(SCRIPT_DIR / "inspect_corpus.py"),
                "--input-dir",
                str(corpus_copy),
                "--out-dir",
                str(inspect_dir),
            ]
        )
        corpus_idx = json.loads((inspect_dir / "corpus_index.json").read_text(encoding="utf-8"))
        xml_recon = json.loads((inspect_dir / "xml_math_reconciliation.json").read_text(encoding="utf-8"))
        assert corpus_idx["unique_paper_count"] == 2, f"Expected 2 unique papers, got {corpus_idx['unique_paper_count']}"
        assert corpus_idx["has_pdf_pages"] is False, "Expected has_pdf_pages == False"
        assert xml_recon["xml_files"][0]["formula_paragraph_count"] >= 3, "Expected >=3 XML formula paragraphs"
        assert xml_recon["xml_files"][0]["table_count"] == 1, "Expected 1 XML table"

        # Step 2: Build Stage-1 KG (kg_v1.json)
        data = build_synthetic_extractions()
        p1_path = work_dir / "primary_extraction.json"
        p2_path = work_dir / "secondary_extraction.json"
        align_path = work_dir / "alignment_decisions.json"
        cross_path = work_dir / "cross_paper_relations.json"
        qspec_path = work_dir / "query_specs.json"
        p1_path.write_text(json.dumps(data["primary"], ensure_ascii=False, indent=2), encoding="utf-8")
        p2_path.write_text(json.dumps(data["secondary"], ensure_ascii=False, indent=2), encoding="utf-8")
        align_path.write_text(json.dumps(data["alignment"], ensure_ascii=False, indent=2), encoding="utf-8")
        cross_path.write_text(json.dumps(data["cross"], ensure_ascii=False, indent=2), encoding="utf-8")
        qspec_path.write_text(json.dumps(data["queries"], ensure_ascii=False, indent=2), encoding="utf-8")

        kg_v1_path = work_dir / "kg_v1.json"
        run_cmd(
            [
                str(SCRIPT_DIR / "build_and_update_kg.py"),
                "--mode",
                "init",
                "--corpus-index",
                str(inspect_dir / "corpus_index.json"),
                "--extraction",
                str(p1_path),
                "--out",
                str(kg_v1_path),
            ]
        )

        # Step 3: Detect alignment & collision candidates
        cand_path = work_dir / "alignment_candidates.json"
        run_cmd(
            [
                str(SCRIPT_DIR / "detect_alignment_candidates.py"),
                "--base-kg",
                str(kg_v1_path),
                "--new-extraction",
                str(p2_path),
                "--out",
                str(cand_path),
            ]
        )
        cands = json.loads(cand_path.read_text(encoding="utf-8"))
        assert cands["candidate_count"] >= 4, f"Expected >=4 alignment candidates, got {cands['candidate_count']}"
        assert cands["high_risk_non_merge_check_count"] >= 2, "Expected >=2 high-risk non-merge flags (Ni-N-C & MS)"

        # Step 4: Incremental update (kg_v1 -> kg_v2) preserving 100% of v1 IDs
        kg_v2_path = work_dir / "kg_v2.json"
        log_path = work_dir / "incremental_update_log.json"
        run_cmd(
            [
                str(SCRIPT_DIR / "build_and_update_kg.py"),
                "--mode",
                "update",
                "--corpus-index",
                str(inspect_dir / "corpus_index.json"),
                "--base-kg",
                str(kg_v1_path),
                "--extraction",
                str(p2_path),
                "--alignment",
                str(align_path),
                "--cross-relations",
                str(cross_path),
                "--out",
                str(kg_v2_path),
                "--log-out",
                str(log_path),
            ]
        )
        kg_v1 = json.loads(kg_v1_path.read_text(encoding="utf-8"))
        kg_v2 = json.loads(kg_v2_path.read_text(encoding="utf-8"))
        assert any(n["entity_type"] == "Concept" for n in kg_v2["nodes"]), "Expected abstract Concept node for non-merged Ni-N-C"

        # Step 5: Execute real graph queries
        queries_out = work_dir / "kg_queries.json"
        run_cmd(
            [
                str(SCRIPT_DIR / "query_kg.py"),
                "--kg",
                str(kg_v2_path),
                "--query-specs",
                str(qspec_path),
                "--out",
                str(queries_out),
            ]
        )
        qres = json.loads(queries_out.read_text(encoding="utf-8"))
        assert qres["executed_query_count"] == 4
        q_statuses = {q["query_id"]: q["status"] for q in qres["queries"]}
        assert q_statuses["Q1"] == "answered"
        assert q_statuses["Q2"] == "answered"
        assert q_statuses["Q3"] == "answered"
        assert q_statuses["Q4"] == "unable_to_determine", f"Expected Q4 unable_to_determine, got {q_statuses['Q4']}"

        # Step 6: Mechanical verification (positive must exit 0 with 100% grounding & 100% v1 ID preservation)
        ver_out = work_dir / "kg_verification_report.json"
        run_cmd(
            [
                str(SCRIPT_DIR / "verify_kg.py"),
                "--kg",
                str(kg_v2_path),
                "--corpus-index",
                str(inspect_dir / "corpus_index.json"),
                "--xml-recon",
                str(inspect_dir / "xml_math_reconciliation.json"),
                "--prev-kg",
                str(kg_v1_path),
                "--candidates",
                str(cand_path),
                "--queries",
                str(queries_out),
                "--out",
                str(ver_out),
                "--strict",
            ]
        )
        vrep = json.loads(ver_out.read_text(encoding="utf-8"))
        assert vrep["passed"] is True
        assert vrep["metrics"]["quote_grounding_rate"] == 1.0
        assert vrep["metrics"]["incremental_id_preservation_rate"] == 1.0
        assert vrep["metrics"]["error_count"] == 0
        assert vrep["metrics"]["warning_count"] == 0

        # Step 7: Export & assemble Harbor deliverables
        run_cmd(
            [
                str(SCRIPT_DIR / "export_and_assemble.py"),
                "--case",
                str(SYNTH_CORPUS / "case.json"),
                "--kg",
                str(kg_v2_path),
                "--queries",
                str(queries_out),
                "--verification",
                str(ver_out),
                "--xml-recon",
                str(inspect_dir / "xml_math_reconciliation.json"),
                "--out-dir",
                str(out_dir),
            ]
        )
        res_json = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
        assert res_json["status"] == "success"
        case_req = json.loads((SYNTH_CORPUS / "case.json").read_text(encoding="utf-8"))["output_contract"]["required"]
        for req_k in case_req:
            assert req_k in res_json and res_json[req_k] is not None, f"Missing contract key: {req_k}"

        # Validate GraphML XML well-formedness & SVG existence + field provenance
        ET.parse(out_dir / "kg.graphml")
        assert (out_dir / "kg_preview.svg").exists()
        assert (out_dir / "kg_report.md").exists()
        assert res_json["sources"].get("field_provenance_by_doc"), "Missing field_provenance_by_doc in result.json['sources']"
        assert res_json["sources"].get("node_edge_evidence_anchors"), "Missing node_edge_evidence_anchors in result.json['sources']"
        assert "Field-Level Metadata Isolation Provenance" in (out_dir / "kg_report.md").read_text(encoding="utf-8")

        # Step 8: Run 7 adversarial negative tests
        neg_passed = run_negative_tests(
            work_dir,
            kg_v1,
            kg_v2,
            inspect_dir / "corpus_index.json",
            inspect_dir / "xml_math_reconciliation.json",
            queries_out,
        )

    print(
        json.dumps(
            {
                "selftest": "PASSED",
                "positive_pipeline_checks": "all_passed",
                "quote_grounding_rate": 1.0,
                "incremental_id_preservation_rate": 1.0,
                "adversarial_negative_gates_passed": f"{neg_passed}/7",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
