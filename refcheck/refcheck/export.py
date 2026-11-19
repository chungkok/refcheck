"""Outputs: EndNote import file (RIS), results report (CSV + Excel)."""

import csv
import re

from .textutil import display_surname, expand_pages

STATUS_ORDER = ["AUTO_MATCH", "MANUAL", "REVIEW", "NO_MATCH", "ERROR"]


def _split_authors(authors):
    """Vancouver 'Patel A, Dogan H, et al' or APA 'Smith, J. A., & Doe, R.'
    -> ['Patel, A', 'Dogan, H'] (et al dropped)."""
    a = re.sub(r",?\s*\bet\s+al\b\.?", "", authors or "").strip(" .,")
    if not a:
        return []
    if re.search(r"\b[A-Z][\w'\-]+,\s+[A-Z]\.", a):           # APA style
        parts = re.split(r"(?<=\.),\s*(?:&\s*|and\s+)?|\s+&\s+", a)
        return [p.strip(" ,&") for p in parts if p.strip(" ,&")]
    out = []
    for p in re.split(r",\s*|;\s*|\s+and\s+|\s*&\s*", a):
        p = p.strip(" .")
        if not p:
            continue
        m = re.match(r"^(.*\S)\s+([A-Z]{1,4})$", p)
        if m:
            out.append(f"{m.group(1)}, {m.group(2)}")
        elif " " in p:
            out.append(p + ",")   # corporate author: EndNote keeps it whole
        else:
            out.append(p)
    return out


def build_records(refs, decisions, fetched, results, start=1):
    """Collapse references into EndNote records (one per unique PMID).

    decisions: {ref_num: (pmid or '', status)}
    fetched:   {pmid: PubMed record dict}
    Returns (records, ref_to_record_number)."""
    records, by_key, ref_to_rec = [], {}, {}
    for ref in refs:
        pmid, status = decisions.get(ref["num"], ("", "NO_MATCH"))
        key = f"pmid:{pmid}" if pmid and pmid in fetched else f"ref:{ref['num']}"
        if key not in by_key:
            by_key[key] = dict(key=key, pmid=pmid if key.startswith("pmid") else "",
                               pub=fetched.get(pmid) if key.startswith("pmid") else None,
                               refs=[], statuses=[], parsed=ref,
                               record_number=start + len(records))
            records.append(by_key[key])
        rec = by_key[key]
        rec["refs"].append(ref)
        rec["statuses"].append(status)
        ref_to_rec[ref["num"]] = rec["record_number"]
    return records, ref_to_rec


def _ris_lines(rec):
    L = []
    add = lambda tag, val: L.append(f"{tag}  - {val}") if val not in (None, "") else None
    nums = ", ".join(str(r["num"]) for r in rec["refs"])
    status = rec["statuses"][0]
    pub = rec["pub"]
    if pub:
        add("TY", "JOUR")
        for a in pub["authors_full"]:
            add("AU", a)
        add("TI", pub["title"])
        add("T2", pub["journal"])
        add("J2", pub["iso_journal"])
        add("PY", pub["year"])
        add("VL", pub["volume"])
        add("IS", pub["issue"])
        sp, ep = expand_pages(pub["pages"])
        add("SP", sp)
        add("EP", ep)
        add("DO", pub["doi"])
        add("AN", pub["pmid"])
        add("C2", pub["pmcid"])
        add("UR", f"https://pubmed.ncbi.nlm.nih.gov/{pub['pmid']}/")
        add("DB", "PubMed")
        add("LA", pub.get("language", ""))
        if pub.get("abstract"):
            add("AB", pub["abstract"].replace("\n", " "))
        for k in pub.get("keywords", []):
            add("KW", k)
    else:
        p = rec["parsed"]
        add("TY", "JOUR" if p["journal"] else "GEN")
        for a in _split_authors(p["authors"]):
            add("AU", a)
        add("TI", p["title"])
        add("T2", p["journal"])
        add("PY", p["year"])
        add("VL", p["volume"])
        add("IS", p["issue"])
        sp, ep = expand_pages(p["pages"])
        add("SP", sp)
        add("EP", ep)
        add("DO", p["doi"])
        if p["doi"]:
            add("UR", f"https://doi.org/{p['doi']}")
    add("LB", nums)                                   # EndNote "Label" field
    add("KW", f"refcheck {status}")
    add("N1", "refcheck " + status + " | manuscript ref " + nums +
        " | original: " + " || ".join(r["text"] for r in rec["refs"]))
    L.append("ER  - ")
    return L


def write_ris(records, path):
    out = []
    for rec in records:
        out.extend(_ris_lines(rec))
        out.append("")
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(out))


def temp_citation_key(rec):
    """Author and year for the EndNote temporary citation, e.g. ('Patel', '2022')."""
    if rec["pub"]:
        au = rec["pub"]["authors_full"][0].split(",")[0] if rec["pub"]["authors_full"] else ""
        yr = rec["pub"]["year"]
    else:
        au, yr = display_surname(rec["parsed"]["authors"]), rec["parsed"]["year"]
    au = re.sub(r"[{}#;]", "", au).strip() or "Anon"
    return au, yr


REPORT_COLS = [
    "ref_no", "status", "final_pmid", "checked_by_you", "endnote_record", "cited_in_text", "score", "title_similarity",
    "match_reasons", "search_methods", "pubmed_link",
    "pubmed_title", "pubmed_first_author", "pubmed_journal", "pubmed_year",
    "pubmed_volume", "pubmed_issue", "pubmed_pages", "pubmed_doi",
    "runner_up_pmid", "runner_up_score",
    "parsed_authors", "parsed_title", "parsed_journal", "parsed_year", "parsed_volume",
    "parsed_issue", "parsed_pages", "parsed_doi", "original_reference", "note",
]


def report_rows(refs, results, decisions, fetched, ref_to_rec):
    rows = []
    for ref in refs:
        res = results.get(ref["num"], {})
        best, second = res.get("best"), res.get("second")
        pmid, status = decisions.get(ref["num"], ("", res.get("status", "NO_MATCH")))
        pub = fetched.get(pmid) if pmid else None
        rows.append({
            "ref_no": ref["num"], "status": status, "final_pmid": pmid,
            "endnote_record": ref_to_rec.get(ref["num"], ""),
            "cited_in_text": ref.get("cited", ""),
            "checked_by_you": ref.get("reviewed", ""),
            "score": best["score"] if best else "",
            "title_similarity": best["title_similarity"] if best else "",
            "match_reasons": best["match_reasons"] if best else "",
            "search_methods": best["search_methods"] if best else "",
            "pubmed_link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            "pubmed_title": pub["title"] if pub else (best["title"] if best else ""),
            "pubmed_first_author": pub["first_author"] if pub else (best["first_author"] if best else ""),
            "pubmed_journal": (pub or best or {}).get("iso_journal", ""),
            "pubmed_year": (pub or best or {}).get("year", ""),
            "pubmed_volume": (pub or best or {}).get("volume", ""),
            "pubmed_issue": (pub or best or {}).get("issue", ""),
            "pubmed_pages": (pub or best or {}).get("pages", ""),
            "pubmed_doi": (pub or best or {}).get("doi", ""),
            "runner_up_pmid": second["pmid"] if second else "",
            "runner_up_score": second["score"] if second else "",
            "parsed_authors": ref["authors"], "parsed_title": ref["title"],
            "parsed_journal": ref["journal"], "parsed_year": ref["year"],
            "parsed_volume": ref["volume"], "parsed_issue": ref["issue"],
            "parsed_pages": ref["pages"], "parsed_doi": ref["doi"],
            "original_reference": ref["text"],
            "note": "; ".join(x for x in (ref.get("parse_note", ""), res.get("note", "")) if x),
        })
    return rows


def write_report_csv(rows, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=REPORT_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_report_xlsx(rows, path, citation_log=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    fills = {"AUTO_MATCH": "D9EAD3", "MANUAL": "CFE2F3", "REVIEW": "FFF2CC",
             "NO_MATCH": "F4CCCC", "ERROR": "EA9999"}
    wb = Workbook()
    ws = wb.active
    ws.title = "References"
    ws.append(REPORT_COLS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([r.get(k, "") for k in REPORT_COLS])
        fill = fills.get(r["status"])
        if fill:
            ws.cell(ws.max_row, 2).fill = PatternFill("solid", fgColor=fill)
        if r["final_pmid"]:
            cell = ws.cell(ws.max_row, REPORT_COLS.index("pubmed_link") + 1)
            cell.hyperlink, cell.font = r["pubmed_link"], Font(color="1155CC", underline="single")
    widths = {"original_reference": 80, "pubmed_title": 60, "parsed_title": 60, "match_reasons": 40}
    for i, k in enumerate(REPORT_COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(k, 14)
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions

    s = wb.create_sheet("Summary")
    s.append(["status", "count"])
    for st in STATUS_ORDER:
        s.append([st, sum(1 for r in rows if r["status"] == st)])
    s.append(["total", len(rows)])

    if citation_log:
        c = wb.create_sheet("In-text citations")
        cols = ["paragraph", "original", "replaced_with", "refs", "problem", "context"]
        c.append(cols)
        for e in citation_log:
            c.append([e.get(k, "") for k in cols])
        c.column_dimensions["F"].width = 90
        c.column_dimensions["C"].width = 50
        for row in c.iter_rows(min_row=2):
            row[5].alignment = Alignment(wrap_text=False)
    wb.save(path)
