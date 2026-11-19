"""Glue between extraction, PubMed matching and the outputs.
Used by both the Streamlit app and the command line tool."""

import re
import zipfile
from pathlib import Path

from .annotate import annotate_docx
from .export import (build_records, report_rows, temp_citation_key, write_report_csv,
                     write_report_xlsx, write_ris)
from .pubmed import match_reference


def run_matching(refs, client, progress=None):
    results = {}
    for i, ref in enumerate(refs, 1):
        results[ref["num"]] = match_reference(client, ref)
        if progress:
            progress(i, len(refs), ref, results[ref["num"]])
    return results


def default_decisions(refs, results, accept_review=False):
    """{ref_num: (pmid, status)}. AUTO matches are accepted; REVIEW only if asked."""
    out = {}
    for ref in refs:
        res = results.get(ref["num"], {})
        st, best = res.get("status", "NO_MATCH"), res.get("best")
        if best and (st == "AUTO_MATCH" or (st == "REVIEW" and accept_review)):
            out[ref["num"]] = (best["pmid"], st)
        else:
            out[ref["num"]] = ("", st)
    return out


def apply_overrides(decisions, results, overrides):
    """overrides: {ref_num: 'pmid' | '' } from the review table. A typed PMID that
    differs from the suggested one is marked MANUAL."""
    out = dict(decisions)
    for num, val in overrides.items():
        val = re.sub(r"\D", "", str(val or ""))
        res = results.get(num) or {}
        orig, best = res.get("status", "NO_MATCH"), res.get("best")
        if not val:
            out[num] = ("", "ERROR" if orig == "ERROR" else "NO_MATCH")
        elif best and best["pmid"] == val and orig == "AUTO_MATCH":
            out[num] = (val, "AUTO_MATCH")
        else:
            out[num] = (val, "MANUAL")      # accepted suggestion or typed PMID
    return out


def build_outputs(extraction, refs, results, decisions, client, outdir, stem,
                  record_start=1, annotate=True, annotate_opts=None, remove_reference_list=True,
                  reviewed=None):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    fetched = {}
    for res in results.values():
        for c in (res.get("best"), res.get("second")):
            if c:
                fetched[c["pmid"]] = c
    need = [p for p, _ in decisions.values() if p and p not in fetched]
    warnings = []
    if need:
        for rec in client.efetch(need):
            fetched[rec["pmid"]] = rec
        missing = [p for p in need if p not in fetched]
        if missing:
            warnings.append("These PMIDs were not found in PubMed and were treated as unmatched: "
                            + ", ".join(missing))
            decisions = {k: (("", "NO_MATCH") if v[0] in missing else v) for k, v in decisions.items()}

    records, ref_to_rec = build_records(refs, decisions, fetched, results, start=record_start)
    for rec in records:
        if len(rec["refs"]) > 1:
            nums = ", ".join(str(r["num"]) for r in rec["refs"])
            warnings.append(f"References {nums} are the same paper (PMID {rec['pmid']}); "
                            f"they share one EndNote record and cite as one.")
    paths = {}
    paths["ris"] = outdir / f"{stem}_endnote.ris"
    write_ris(records, paths["ris"])

    citation_log = []
    has_body = bool(extraction.get("heading_para"))   # text before the reference heading
    if annotate and extraction.get("docx_path") and has_body:
        last_para = max((r["para_end"] for r in refs if r.get("para_end") is not None), default=None)
        rec_keys = {rec["record_number"]: temp_citation_key(rec) for rec in records}
        paths["docx"] = outdir / f"{stem}_endnote_ready.docx"
        citation_log, w = annotate_docx(
            extraction["docx_path"], paths["docx"], ref_to_rec, rec_keys,
            heading_para=extraction["heading_para"], reflist_end_para=last_para,
            remove_reference_list=remove_reference_list, **(annotate_opts or {}))
        warnings += w
        converted = [e for e in citation_log if e["replaced_with"]]
        if not converted:
            warnings.append("No numbered in-text citations were found to convert. Check the citation "
                            "options: parentheses (1,2) and numbers attached to words (text1,2) "
                            "are separate settings.")
        else:
            cited = {int(n) for e in converted for n in e["refs"].split(",") if n}
            uncited = [r["num"] for r in refs if r["num"] not in cited]
            if uncited:
                warnings.append("Listed in the references but not cited in the text: "
                                + ", ".join(map(str, uncited))
                                + ". Check the In-text citations sheet for anything left unchanged.")
    for row_ref in refs:
        row_ref["cited"] = ""
    if citation_log:
        cited = {int(n) for e in citation_log if e["replaced_with"] for n in e["refs"].split(",") if n}
        for row_ref in refs:
            row_ref["cited"] = "yes" if row_ref["num"] in cited else "no"

    for row_ref in refs:
        row_ref["reviewed"] = "yes" if (reviewed or {}).get(row_ref["num"]) else ""
    rows = report_rows(refs, results, decisions, fetched, ref_to_rec)
    pm = [p for p, _ in decisions.values() if p]
    paths["pmids"] = outdir / f"{stem}_pmids.txt"
    paths["pmids"].write_text("\n".join(dict.fromkeys(pm)) + ("\n" if pm else ""), encoding="utf-8")
    paths["csv"] = outdir / f"{stem}_pubmed_report.csv"
    paths["xlsx"] = outdir / f"{stem}_pubmed_report.xlsx"
    write_report_csv(rows, paths["csv"])
    write_report_xlsx(rows, paths["xlsx"], citation_log)

    paths["zip"] = outdir / f"{stem}_refcheck.zip"
    with zipfile.ZipFile(paths["zip"], "w", zipfile.ZIP_DEFLATED) as z:
        for k, p in paths.items():
            if k != "zip":
                z.write(p, p.name)
    return dict(paths=paths, rows=rows, citation_log=citation_log, warnings=warnings,
                n_records=len(records), ref_to_rec=ref_to_rec)
