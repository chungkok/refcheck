"""Glue between extraction, PubMed matching and the outputs.
Used by both the Streamlit app and the command line tool."""

import re
import zipfile
from pathlib import Path

from .annotate import annotate_docx, document_structure
from .audit import audit_findings, citation_audit
from .export import (build_records, change_report, report_rows, temp_citation_key, validate_ris, write_bibtex,
                     write_endnote_xml, write_report_csv, write_report_xlsx, write_ris)
from .verify import find_duplicates
from .styles import FAMILIES, detect_reference_style, manuscript_style
from .pubmed import NCBIError, match_reference
from .report import build_report, write_html, write_json
from .verify import classify_all, summarise


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
                  reviewed=None, report_meta=None, evidence=None):
    """Write every output file into `outdir` and return paths plus all findings.

    report_meta (optional): input_name, input_sha256, started, overrides, deployment.
    evidence (optional): a sources.Evidence; when given, secondary sources (Crossref,
    OpenAlex, doi.org, NLM Catalog, DOAJ, Unpaywall, Retraction Watch) add findings.
    When given, the JSON and HTML integrity reports are written too."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    fetched = {}
    for res in results.values():
        for c in (res.get("best"), res.get("second")):
            if c:
                fetched[c["pmid"]] = c
    need = [p for p, _ in decisions.values() if p and p not in fetched]
    warnings = []
    # records that a written DOI or PMID points to, so mismatches can name them
    for res in results.values():
        for pm_list in (res.get("identifier_hits") or {}).values():
            need += [p for p in pm_list if p not in fetched and p not in need]
    if need:
        try:
            for rec in client.efetch(need):
                fetched[rec["pmid"]] = rec
        except NCBIError as e:
            warnings.append(f"Could not fetch some PubMed records ({e.kind}); they are reported as check errors.")
        chosen = {p for p, _ in decisions.values() if p}
        missing = [p for p in need if p not in fetched and p in chosen]
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
    ris_problems = validate_ris(paths["ris"], expected_records=len(records))
    if ris_problems:
        warnings.append("RIS check: " + "; ".join(ris_problems[:5])
                        + (f" (and {len(ris_problems) - 5} more)" if len(ris_problems) > 5 else ""))
    paths["endnote_xml"] = outdir / f"{stem}_endnote.xml"
    write_endnote_xml(records, paths["endnote_xml"])
    paths["bib"] = outdir / f"{stem}_references.bib"
    write_bibtex(records, paths["bib"])

    citation_log, changes, audit, stats = [], None, None, {}
    has_body = bool(extraction.get("heading_para"))   # text before the reference heading
    if extraction.get("docx_path") and has_body:
        last_para = max((r["para_end"] for r in refs if r.get("para_end") is not None), default=None)
        rec_keys = {rec["record_number"]: temp_citation_key(rec) for rec in records}
        out_docx = outdir / f"{stem}_endnote_ready.docx"
        citation_log, w = annotate_docx(
            extraction["docx_path"], out_docx, ref_to_rec, rec_keys,
            heading_para=extraction["heading_para"], reflist_end_para=last_para,
            body_start_para=extraction.get("body_start_para"),
            remove_reference_list=remove_reference_list, stats=stats, **(annotate_opts or {}))
        removed = (last_para - extraction["heading_para"]) if (remove_reference_list and last_para) else 0
        changes = change_report(citation_log, document_structure(extraction["docx_path"]),
                                document_structure(out_docx), removed, stats.get("removed_tables", 0),
                                stats.get("removed_fields"))
        if changes["structure_preserved"] is False:
            bad = [x["item"] for x in changes["structure"] if not x["preserved"]]
            warnings.append("Document structure check: these counts changed unexpectedly: " + ", ".join(bad))
        if annotate:
            paths["docx"] = out_docx
            warnings += w
        else:
            out_docx.unlink()             # audit only: no Word copy delivered
        converted = [e for e in citation_log if e["replaced_with"]]
        cited_by = converted + [e for e in citation_log if e.get("existing")]   # EndNote fields cite too
        if not annotate:
            pass
        elif not cited_by:
            warnings.append("No numbered in text citations were found to convert. Check the citation "
                            "options: parentheses (1,2) and numbers attached to words (text1,2) "
                            "are separate settings.")
        else:
            cited = {int(n) for e in cited_by for n in e["refs"].split(",") if n}
            uncited = [r["num"] for r in refs if r["num"] not in cited]
            if uncited:
                warnings.append("Listed in the references but not cited in the text: "
                                + ", ".join(map(str, uncited))
                                + ". Check the In text citations sheet for anything left unchanged.")
    for row_ref in refs:
        row_ref["cited"] = ""
    if citation_log:
        cited = {int(n) for e in citation_log if e["replaced_with"] for n in e["refs"].split(",") if n}
        for row_ref in refs:
            row_ref["cited"] = "yes" if row_ref["num"] in cited else "no"

    for row_ref in refs:
        row_ref["reviewed"] = "yes" if (reviewed or {}).get(row_ref["num"]) else ""
    ext_ev, ext_journals = {}, {}
    if evidence is not None:
        from .sources import gather
        ext_ev, ext_journals = gather(refs, decisions, fetched, evidence)
        if evidence.errors:
            uniq = list(dict.fromkeys(evidence.errors))
            warnings.append(f"{len(evidence.errors)} secondary source lookups failed; their checks are "
                            "missing, not passed: " + "; ".join(uniq[:4]) + (" ..." if len(uniq) > 4 else ""))
    integrity = classify_all(refs, results, decisions, fetched, reviewed, ext_ev, ext_journals)
    unchecked = sorted(n for n, it in integrity.items() if it["status"] == "CHECK_ERROR")
    if unchecked:
        warnings.append(f"{len(unchecked)} references could not be checked because PubMed did not answer "
                        "(network or service problem); they are CHECK_ERROR, not verified. Run again to check "
                        "them: references " + ", ".join(map(str, unchecked[:30])) + (" ..." if len(unchecked) > 30 else ""))
    if citation_log:
        audit = citation_audit(citation_log, refs, extraction.get("warnings"),
                               find_duplicates(refs, decisions))
    style = manuscript_style(refs)
    if style["mixed"]:
        warnings.append(style["message"])
    rows = report_rows(refs, results, decisions, fetched, ref_to_rec)
    for row in rows:
        det = detect_reference_style(next((r["text"] for r in refs if r["num"] == row["ref_no"]), ""))
        row["detected_style_family"] = FAMILIES[det["family"]]
        row["detected_style"] = det["style"]
        it = integrity[row["ref_no"]]
        row["integrity_status"] = it["status"]
        row["integrity_flags"] = ", ".join(it["flags"])
        row["findings"] = " | ".join(f"{f['code']}: {f['detail']}" for f in it["findings"])
        row["decided_by"] = it["decided_by"]
        res = results.get(row["ref_no"], {})
        row["abstained_because"] = "; ".join(res.get("abstain") or [])
        row["open_access_url"] = (ext_ev.get(row["ref_no"]) or {}).get("oa_url") or ""
    pm = [p for p, _ in decisions.values() if p]
    paths["pmids"] = outdir / f"{stem}_pmids.txt"
    paths["pmids"].write_text("\n".join(dict.fromkeys(pm)) + ("\n" if pm else ""), encoding="utf-8")
    paths["csv"] = outdir / f"{stem}_pubmed_report.csv"
    paths["xlsx"] = outdir / f"{stem}_pubmed_report.xlsx"
    write_report_csv(rows, paths["csv"])
    write_report_xlsx(rows, paths["xlsx"], citation_log)

    result = dict(paths=paths, rows=rows, citation_log=citation_log, warnings=warnings,
                  reading_warnings=list(extraction.get("warnings") or []),
                  n_records=len(records), ref_to_rec=ref_to_rec, integrity=integrity,
                  integrity_summary=summarise(integrity), records=fetched, decisions=decisions,
                  audit=audit, audit_findings=audit_findings(audit) if audit else [],
                  changes=changes, ris_problems=ris_problems, reference_style=style,
                  existing_fields=stats.get("field_notes", {}), source_kind=extraction.get("source_kind", ""),
                  evidence=ext_ev, journal_evidence=ext_journals,
                  sources_used=sorted(evidence.enabled) + (["retraction-watch-csv"] if evidence.rw else [])
                  if evidence is not None else [],
                  retraction_watch=(evidence.rw_status.public() if getattr(evidence, "rw_status", None)
                                    else ({"status": "user file", "path": evidence.rw.path.name} if evidence.rw
                                          else None)) if evidence is not None else None)
    if report_meta is not None:
        rep = build_report(refs=refs, results=results, outputs=result,
                           settings=getattr(client, "settings", None), network=getattr(client, "audit", None),
                           reviewed=reviewed, **report_meta)
        paths["json"] = outdir / f"{stem}_integrity_report.json"
        paths["html"] = outdir / f"{stem}_integrity_report.html"
        write_json(rep, paths["json"])
        write_html(rep, paths["html"])
        result["report"] = rep

    paths["zip"] = outdir / f"{stem}_refcheck.zip"
    with zipfile.ZipFile(paths["zip"], "w", zipfile.ZIP_DEFLATED) as z:
        for k, p in paths.items():
            if k != "zip" and Path(p).exists():
                z.write(p, p.name)
    return result
