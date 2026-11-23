"""RefAudit: manuscript wide citation to reference reconciliation.

Works from the citation log the Word converter produces (every candidate citation it
found, converted or not) plus the parsed reference list. Produces a mapping graph,
citations without a reference, references never cited, first citation order,
numbering problems, mixed citation styles and ambiguous numbers left for a person.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional


def _nums(entry: Dict[str, Any]) -> List[int]:
    return [int(n) for n in (entry.get("refs") or "").split(",") if n.strip().isdigit()]


def citation_audit(citation_log: List[Dict[str, Any]], refs: List[Dict[str, Any]],
                   extraction_warnings: Optional[List[str]] = None,
                   duplicates: Optional[Dict[int, int]] = None) -> Dict[str, Any]:
    ref_nums = [r["num"] for r in refs]
    known = set(ref_nums)
    edges, without_ref, ambiguous = [], [], []
    # document order (the converter logs some citations right to left within a paragraph)
    citation_log = sorted(citation_log, key=lambda e: tuple(e.get("order") or (0, 0)))
    for i, e in enumerate(citation_log, 1):
        nums = _nums(e)
        cid = f"C{i}"
        problem = e.get("problem", "")
        if e.get("replaced_with") or e.get("existing"):
            edges.append(dict(citation=cid, location=e.get("location", "body"), paragraph=e["paragraph"],
                              form=e.get("form", ""), original=e["original"], refs=nums,
                              converted=bool(e.get("replaced_with"))))
        elif "outside the reference list" in problem or "not found" in problem:
            without_ref.append(dict(citation=cid, paragraph=e["paragraph"], original=e["original"],
                                    missing=[n for n in nums if n not in known] or nums,
                                    context=e.get("context", "")))
        else:
            ambiguous.append(dict(citation=cid, paragraph=e["paragraph"], original=e["original"],
                                  reason=problem, context=e.get("context", "")))

    cited_counts = Counter(n for ed in edges for n in ed["refs"])
    uncited = [n for n in ref_nums if n not in cited_counts]

    # Vancouver convention: references are numbered in order of first citation.
    first_seen: List[int] = []
    for ed in edges:
        for n in ed["refs"]:
            if n not in first_seen:
                first_seen.append(n)
    # flagged when a lower numbered reference is first cited after it (1, 2, 5, 3, 4 flags 5)
    out_of_order = [n for i, n in enumerate(first_seen) if any(m < n for m in first_seen[i + 1:])]

    numbering = [w for w in (extraction_warnings or []) if "Numbering" in w or "numbered" in w]
    expected = list(range(1, len(ref_nums) + 1))
    if ref_nums and ref_nums != expected:
        numbering.append("Reference numbers are not a continuous 1..N sequence.")

    forms = Counter(ed["form"] for ed in edges if ed["form"] and ed["converted"])
    mixed = None
    if len(forms) > 1:
        main, n_main = forms.most_common(1)[0]
        others = {k: v for k, v in forms.items() if k != main}
        mixed = dict(main=main, main_count=n_main, others=others)

    locations = Counter(ed["location"] for ed in edges)
    return dict(
        citations_converted=sum(1 for ed in edges if ed["converted"]),
        citations_in_existing_fields=sum(1 for ed in edges if not ed["converted"]),
        citations_without_reference=without_ref,
        ambiguous_left_unchanged=ambiguous,
        uncited_references=uncited,
        duplicate_references=[dict(reference=k, same_as=v) for k, v in sorted((duplicates or {}).items())],
        first_citation_out_of_order=out_of_order,
        numbering_problems=numbering,
        mixed_citation_styles=mixed,
        citations_by_location=dict(locations),
        times_cited={n: cited_counts.get(n, 0) for n in ref_nums},
        mapping=edges,
    )


def audit_findings(audit: Dict[str, Any]) -> List[str]:
    """Plain language lines for the app and the report."""
    out = []
    if audit["citations_without_reference"]:
        nums = sorted({n for c in audit["citations_without_reference"] for n in c["missing"]})
        out.append(f"{len(audit['citations_without_reference'])} citations point to numbers that are "
                   f"not in the reference list: {', '.join(map(str, nums))}.")
    if audit["uncited_references"]:
        out.append("References never cited in the text: " + ", ".join(map(str, audit["uncited_references"])) + ".")
    if audit["duplicate_references"]:
        out.append("Duplicate references: " + ", ".join(f"{d['reference']} repeats {d['same_as']}"
                                                         for d in audit["duplicate_references"]) + ".")
    if audit["first_citation_out_of_order"]:
        out.append("First cited out of numerical order (matters for Vancouver style): "
                   + ", ".join(map(str, audit["first_citation_out_of_order"])) + ".")
    for w in audit["numbering_problems"]:
        out.append(w)
    m = audit["mixed_citation_styles"]
    if m:
        out.append(f"Mixed citation styles: mostly {m['main']} ({m['main_count']}), also "
                   + ", ".join(f"{k} ({v})" for k, v in m["others"].items()) + ".")
    if audit["ambiguous_left_unchanged"]:
        out.append(f"{len(audit['ambiguous_left_unchanged'])} numbers looked like citations but were "
                   "ambiguous and left unchanged for you to check.")
    return out
