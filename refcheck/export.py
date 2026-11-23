"""Outputs: EndNote import file (RIS), results report (CSV + Excel)."""

import csv
import re

from .textutil import display_surname, expand_pages

STATUS_ORDER = ["AUTO_MATCH", "MANUAL", "REVIEW", "NO_MATCH", "ERROR"]
INTEGRITY_ORDER = ["VERIFIED", "VERIFIED_WITH_WARNINGS", "METADATA_ERROR", "DOI_MISMATCH",
                   "PMID_MISMATCH", "DUPLICATE", "RETRACTED", "CORRECTED", "EXPRESSION_OF_CONCERN",
                   "PREPRINT", "NOT_FOUND_IN_PUBMED", "NON_PUBMED_SOURCE", "UNRESOLVED", "CHECK_ERROR"]
INTEGRITY_FILLS = {"VERIFIED": "D9EAD3", "VERIFIED_WITH_WARNINGS": "E6F2D9", "METADATA_ERROR": "FCE5CD",
                   "DOI_MISMATCH": "F4CCCC", "PMID_MISMATCH": "F4CCCC", "DUPLICATE": "FCE5CD",
                   "RETRACTED": "E06666", "EXPRESSION_OF_CONCERN": "EA9999", "CORRECTED": "FFF2CC",
                   "PREPRINT": "CFE2F3", "NOT_FOUND_IN_PUBMED": "EFEFEF", "NON_PUBMED_SOURCE": "EFEFEF",
                   "UNRESOLVED": "FFF2CC", "CHECK_ERROR": "EA9999"}


def _split_authors(authors):
    """Vancouver 'Patel A, Dogan H, et al' or APA 'Smith, J. A., & Doe, R.'
    -> ['Patel, A', 'Dogan, H'] (et al dropped)."""
    a = re.sub(r",?\s*\bet\s+al\b\.?", "", authors or "").strip(" .,")
    if not a:
        return []
    if re.search(r"\b[A-Z][\w'\-]+,\s+[A-Z]\.", a):           # APA style
        parts = re.split(r"(?<=\.),\s*(?:&\s*|and\s+)?|\s+&\s+", a)
        return [p.strip(" ,&") for p in parts if p.strip(" ,&")]
    suffix = r"(?:Jr|Sr|2nd|3rd|[4-9]th|II|III|IV|V)"
    out = []
    for p in re.split(r",\s*|;\s*|\s+and\s+|\s*&\s*", a):
        p = p.strip(" .")
        if not p:
            continue
        if re.fullmatch(suffix, p) and out:          # "Roberts WJ, 2nd": suffix of the previous name
            out[-1] = out[-1].rstrip(",") + f", {p}"
            continue
        m = re.match(r"^(.*\S)\s+([A-Z]{1,4})(?:\s+(" + suffix + r"))?$", p)
        if m:
            out.append(f"{m.group(1)}, {m.group(2)}" + (f", {m.group(3)}" if m.group(3) else ""))
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


_TYPES = {  # source_type label -> (RIS type, EndNote XML type name, EndNote XML type number)
    "software or code repository": ("COMP", "Computer Program", 9),
    "book or book chapter": ("BOOK", "Book", 6),
    "book (place: publisher; year)": ("BOOK", "Book", 6),
    "thesis": ("THES", "Thesis", 32),
    "conference material": ("CONF", "Conference Proceedings", 10),
    "report or guideline": ("RPRT", "Report", 27),
    "web page or online resource": ("ELEC", "Web Page", 12),
}


def _source_fields(rec):
    """Record type and the extra fields of a reference that is not in PubMed: a book gets BOOK
    (or CHAP for 'In: ...'), with publisher, place and edition; a web page ELEC with its URL and
    access date; a report RPRT; and so on. A journal article stays JOUR."""
    from .verify import source_type
    p = rec["parsed"]
    text = rec["refs"][0].get("text", "") if rec.get("refs") else p.get("text", "")
    kind, label = source_type(dict(p, text=text))
    out = dict(ris="JOUR" if p.get("journal") else "GEN", xml=("Journal Article", 17) if p.get("journal")
               else ("Generic", 13), url="", publisher="", place="", edition="", accessed="")
    if kind == "non_pubmed" and label in _TYPES:
        ris, name, num = _TYPES[label]
        if ris == "BOOK" and re.search(r"\bIn:\s", text):
            ris, name, num = "CHAP", "Book Section", 5
        out.update(ris=ris, xml=(name, num))
    u = re.search(r"https?://(?!(?:dx\.)?doi\.org)[^\s<>\]]+", text)
    if u:
        out["url"] = u.group(0).rstrip(".,;)")
    pp = re.search(r"\.\s*([A-Z][^.:;]{1,60}):\s*([^;.]{2,90});\s*(?:19|20)\d\d", text)
    if pp and out["ris"] in ("BOOK", "CHAP", "RPRT", "THES", "COMP", "GEN", "ELEC"):
        out["place"], out["publisher"] = pp.group(1).strip(), pp.group(2).strip()
    ed = re.search(r"\b(\d+(?:st|nd|rd|th))\s+ed\b", text, re.I)
    if ed:
        out["edition"] = ed.group(1)
    out["book_title"] = ""
    if out["ris"] == "CHAP":       # 'In: Hoffbrand AV, editor. Postgraduate haematology. 8th ed. ...'
        bt = re.search(r"\bIn:\s*(?:[^.]*?\b(?:editors?|eds?)\.?\s+)?([^.]{3,150})\.", text)
        if bt:
            out["book_title"] = bt.group(1).strip()
    ac = re.search(r"\b(?:Accessed|cited)\s+([^.;\]]{4,30})", text, re.I)
    if ac:
        out["accessed"] = ac.group(1).strip()
    return out


def _ris_lines(rec):
    L = []
    add = lambda tag, val: L.append(f"{tag}  - {val}") if val not in (None, "") else None
    nums = ", ".join(str(r["num"]) for r in rec["refs"])
    status = rec["statuses"][0]
    pub = rec["pub"]
    if pub:
        add("TY", "JOUR")
        for a in pub["authors_full"]:
            # a group author from PubMed ("Cancer Genome Atlas Research Network") has no comma;
            # EndNote reads a trailing comma as "corporate author, keep whole"
            add("AU", a if "," in a else a.rstrip() + ",")
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
        sf = _source_fields(rec)
        add("TY", sf["ris"])
        for a in _split_authors(p["authors"]):
            add("AU", a)
        add("TI", p["title"])
        add("T2", p["journal"] or sf["book_title"])
        add("ET", sf["edition"])
        add("PB", sf["publisher"])
        add("CY", sf["place"])
        add("Y2", sf["accessed"])
        add("PY", p["year"])
        add("VL", p["volume"])
        add("IS", p["issue"])
        sp, ep = expand_pages(p["pages"])
        add("SP", sp)
        add("EP", ep)
        add("DO", p["doi"])
        if p["doi"]:
            add("UR", f"https://doi.org/{p['doi']}")
        add("UR", sf["url"])
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
    "ref_no", "integrity_status", "integrity_flags", "status", "final_pmid", "decided_by", "checked_by_you", "endnote_record", "cited_in_text", "score", "title_similarity",
    "match_reasons", "search_methods", "pubmed_link",
    "pubmed_title", "pubmed_first_author", "pubmed_authors", "pubmed_journal", "pubmed_year", "pubmed_online_year",
    "pubmed_volume", "pubmed_issue", "pubmed_pages", "pubmed_doi",
    "runner_up_pmid", "runner_up_score",
    "parsed_authors", "parsed_title", "parsed_journal", "parsed_year", "parsed_volume",
    "parsed_issue", "parsed_pages", "parsed_doi", "original_reference", "detected_style_family",
    "detected_style", "findings",
    "abstained_because", "suggested_reference", "open_access_url", "note",
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
            "pubmed_authors": (pub or best or {}).get("authors", ""),
            "pubmed_journal": (pub or best or {}).get("iso_journal", ""),
            "pubmed_online_year": (pub or best or {}).get("epub_year", ""),
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
            "suggested_reference": vancouver(pub),
            "note": "; ".join(x for x in (ref.get("parse_note", ""), res.get("note", "")) if x),
        })
    return rows


def vancouver(pub):
    """Reference text in Vancouver (NLM) style built from a PubMed record.

    Six authors then 'et al.', as in the NLM Citing Medicine recommendation."""
    if not pub:
        return ""
    au = [a.strip() for a in (pub.get("authors") or "").split(";") if a.strip()]
    names = ", ".join(au[:6]) + (", et al" if len(au) > 6 else "")
    title = (pub.get("title") or "").rstrip(".")
    j = (pub.get("iso_journal") or pub.get("journal") or "").replace(".", "")
    part = pub.get("year", "")
    if pub.get("volume"):
        part += f";{pub['volume']}"
        if pub.get("issue"):
            part += f"({pub['issue']})"
    if pub.get("pages"):
        part += f":{pub['pages']}"
    out = ". ".join(x for x in (names, title, j, part) if x) + "."
    if pub.get("doi"):
        out += f" doi:{pub['doi']}"
    return out


def _bib_escape(v):
    return re.sub(r"([{}])", r"\\\1", str(v)).replace("&", "\\&").replace("%", "\\%")


def bibtex_key(rec, used):
    au, yr = temp_citation_key(rec)
    base = re.sub(r"[^A-Za-z0-9]", "", au) or "ref"
    key = f"{base}{yr}"
    k, i = key, 0
    while k in used:
        i += 1
        k = key + "abcdefghijklmnopqrstuvwxyz"[(i - 1) % 26] + ("" if i <= 26 else str(i))
    used.add(k)
    return k


def write_bibtex(records, path):
    """BibTeX file of the records (PubMed metadata when matched, parsed fields otherwise)."""
    used, out = set(), []
    for rec in records:
        pub, p = rec["pub"], rec["parsed"]
        f = {}
        if pub:
            f["author"] = " and ".join(pub.get("authors_full") or [])
            f.update(title=pub["title"], journal=pub["journal"], year=pub["year"], volume=pub["volume"],
                     number=pub["issue"], pages=pub["pages"].replace("-", "--"), doi=pub["doi"],
                     pmid=pub["pmid"], pmcid=pub.get("pmcid", ""))
            typ = "article"
        else:
            f["author"] = " and ".join(_split_authors(p["authors"])).replace(",,", ",").rstrip(",")
            f.update(title=p["title"], journal=p["journal"], year=p["year"], volume=p["volume"],
                     number=p["issue"], pages=(p["pages"] or "").replace("-", "--"), doi=p["doi"])
            typ = "article" if p["journal"] else "misc"
        f["note"] = "refcheck " + rec["statuses"][0] + "; manuscript ref " + ", ".join(str(r["num"]) for r in rec["refs"])
        body = ",\n".join(f"  {k} = {{{_bib_escape(v)}}}" for k, v in f.items() if v not in (None, ""))
        out.append(f"@{typ}{{{bibtex_key(rec, used)},\n{body}\n}}\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


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
            ws.cell(ws.max_row, REPORT_COLS.index("status") + 1).fill = PatternFill("solid", fgColor=fill)
        ifill = INTEGRITY_FILLS.get(r.get("integrity_status", ""))
        if ifill:
            ws.cell(ws.max_row, REPORT_COLS.index("integrity_status") + 1).fill = PatternFill("solid", fgColor=ifill)
        if r["final_pmid"]:
            cell = ws.cell(ws.max_row, REPORT_COLS.index("pubmed_link") + 1)
            cell.hyperlink, cell.font = r["pubmed_link"], Font(color="1155CC", underline="single")
    widths = {"original_reference": 80, "pubmed_title": 60, "parsed_title": 60, "match_reasons": 40,
              "integrity_status": 24, "integrity_flags": 30, "findings": 80}
    for i, k in enumerate(REPORT_COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(k, 14)
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions

    s = wb.create_sheet("Summary")
    s.append(["integrity status", "count"])
    for st in INTEGRITY_ORDER:
        n = sum(1 for r in rows if r.get("integrity_status") == st)
        if n:
            s.append([st, n])
    s.append([])
    s.append(["match status", "count"])
    for st in STATUS_ORDER:
        s.append([st, sum(1 for r in rows if r["status"] == st)])
    s.append(["total", len(rows)])
    s.append([])
    s.append(["Not in PubMed does not mean fabricated. Books, guidelines, software and many journals "
              "are outside PubMed."])

    if citation_log:
        c = wb.create_sheet("In text citations")
        cols = ["paragraph", "original", "replaced_with", "refs", "problem", "context"]
        c.append(cols)
        for e in citation_log:
            c.append([e.get(k, "") for k in cols])
        c.column_dimensions["F"].width = 90
        c.column_dimensions["C"].width = 50
        for row in c.iter_rows(min_row=2):
            row[5].alignment = Alignment(wrap_text=False)
    wb.save(path)


# ------------------------------------------------------------ RIS checks ----

RIS_TAG = re.compile(r"^([A-Z][A-Z0-9])  - (.*)$")


def read_ris(path):
    """Parse a RIS file into a list of {tag: [values]} records."""
    recs, cur = [], None
    for ln in open(path, encoding="utf-8-sig").read().splitlines():
        if not ln.strip():
            continue
        m = RIS_TAG.match(ln)
        if not m:
            raise ValueError(f"not a RIS line: {ln[:60]!r}")
        tag, val = m.group(1), m.group(2)
        if tag == "TY":
            cur = {"TY": [val]}
        elif tag == "ER":
            recs.append(cur)
            cur = None
        elif cur is not None:
            cur.setdefault(tag, []).append(val)
    if cur is not None:
        raise ValueError("last record has no ER line")
    return recs


def validate_ris(path, expected_records=None):
    """Problems with an exported RIS file (empty list = valid)."""
    problems = []
    try:
        recs = read_ris(path)
    except ValueError as e:
        return [str(e)]
    if expected_records is not None and len(recs) != expected_records:
        problems.append(f"{len(recs)} records in the file, {expected_records} expected")
    for i, r in enumerate(recs, 1):
        if not r.get("TI"):
            problems.append(f"record {i}: no title")
        if not r.get("LB"):
            problems.append(f"record {i}: no Label (manuscript reference number)")
        for y in r.get("PY", []):
            if not re.fullmatch(r"(?:19|20)\d{2}", y):
                problems.append(f"record {i}: year '{y}' is not four digits")
        for a in r.get("AU", []):
            if "," not in a or re.fullmatch(r"(?:Jr|Sr|2nd|3rd|[4-9]th|II|III|IV|V),?", a.strip()):
                problems.append(f"record {i}: author '{a}' is not 'Last, First'")
        for k, vals in r.items():
            if any(v.strip() == "" for v in vals):
                problems.append(f"record {i}: empty {k} field")
        if r.get("AN") and not all(v.isdigit() for v in r["AN"]):
            problems.append(f"record {i}: PMID in AN is not numeric")
    return problems


# ----------------------------------------------------- EndNote XML export ---

def write_endnote_xml(records, path):
    """EndNote XML (File > Import, option 'EndNote generated XML').
    An alternative to RIS; it keeps the Label and PMID in named fields."""
    from xml.sax.saxutils import escape as esc  # nosec B406 - escaping output, not parsing

    def tag(name, val):
        return f"<{name}><style face=\"normal\" font=\"default\" size=\"100%\">{esc(str(val))}</style></{name}>" if val else ""

    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<xml><records>"]
    for rec in records:
        pub, p = rec["pub"], rec["parsed"]
        nums = ", ".join(str(r["num"]) for r in rec["refs"])
        status = rec["statuses"][0]
        if pub:
            authors, title, journal, iso = pub["authors_full"], pub["title"], pub["journal"], pub["iso_journal"]
            year, vol, iss, pages, doi, pmid, abstract = (pub["year"], pub["volume"], pub["issue"],
                                                         pub["pages"], pub["doi"], pub["pmid"], pub.get("abstract", ""))
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        else:
            authors, title, journal, iso = _split_authors(p["authors"]), p["title"], p["journal"], ""
            year, vol, iss, pages, doi, pmid, abstract = p["year"], p["volume"], p["issue"], p["pages"], p["doi"], "", ""
            url = f"https://doi.org/{doi}" if doi else ""
        sf = _source_fields(rec) if not pub else dict(xml=("Journal Article", 17), url="", publisher="", place="",
                                                       edition="", accessed="", book_title="")
        journal = journal or sf["book_title"]
        url = url or sf["url"]
        sp, ep = expand_pages(pages)
        pg = f"{sp}-{ep}" if ep else sp
        out.append("<record>")
        out.append(f'<ref-type name="{sf["xml"][0]}">{sf["xml"][1]}</ref-type>')
        out.append("<contributors><authors>" + "".join(tag("author", a) for a in authors) + "</authors></contributors>")
        out.append("<titles>" + tag("title", title) + tag("secondary-title", journal) + tag("alt-title", iso) + "</titles>")
        out.append("<periodical>" + tag("full-title", journal) + tag("abbr-1", iso) + "</periodical>")
        out.append(tag("pages", pg) + tag("volume", vol) + tag("number", iss) + tag("edition", sf["edition"])
                   + tag("publisher", sf["publisher"]) + tag("pub-location", sf["place"])
                   + tag("access-date", sf["accessed"]))
        out.append("<keywords>" + tag("keyword", f"refcheck {status}") + "</keywords>")
        out.append("<dates>" + tag("year", year) + "</dates>")
        out.append(tag("accession-num", pmid) + tag("electronic-resource-num", doi) + tag("label", nums))
        if abstract:
            out.append(tag("abstract", abstract.replace("\n", " ")))
        out.append(tag("notes", f"refcheck {status} | manuscript ref {nums} | original: "
                       + " || ".join(r["text"] for r in rec["refs"])))
        if url:
            out.append("<urls><related-urls>" + tag("url", url) + "</related-urls></urls>")
        out.append("</record>")
    out.append("</records></xml>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


# --------------------------------------------------------- change report ----

def change_report(citation_log, before_structure=None, after_structure=None, removed_reflist_paras=0,
                  removed_reflist_tables=0, removed_reflist_fields=None):
    """Every change RefCheck made to the Word copy, and a structural comparison."""
    changes = [dict(location=e.get("location", "body"), paragraph=e["paragraph"], form=e.get("form", ""),
                    before=e["original"], after=e["replaced_with"] or e["original"],
                    changed=bool(e["replaced_with"]), references=e["refs"],
                    reason="" if e["replaced_with"] else e.get("problem") or e.get("note", ""))
               for e in citation_log]
    structure = []
    if before_structure and after_structure:
        for k in ("paragraphs", "tables", "images", "sections", "footnotes", "endnotes", "equations"):
            b, a = before_structure.get(k), after_structure.get(k)
            exp = b - removed_reflist_paras if k == "paragraphs" else b - removed_reflist_tables if k == "tables" else b
            structure.append(dict(item=k, original=b, output=a, expected=exp, preserved=a == exp))
        for k, b in before_structure["fields"].items():
            a = after_structure["fields"].get(k, 0)
            exp = b - (removed_reflist_fields or {}).get(k, 0)      # e.g. EndNote's EN.REFLIST field
            if b or a:
                structure.append(dict(item=f"{k} fields", original=b, output=a, expected=exp, preserved=a == exp))
    return dict(changes=changes, structure=structure,
                n_changed=sum(1 for c in changes if c["changed"]),
                n_unchanged=sum(1 for c in changes if not c["changed"]),
                structure_preserved=all(s["preserved"] for s in structure) if structure else None)
