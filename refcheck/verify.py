"""RefVerify: bibliographic integrity classification.

Runs after matching and after the user's review. For each reference it compares the
supplied metadata with the chosen PubMed record and with what the written DOI and
PMID point to, reads PubMed's publication types and CommentsCorrections links, and
returns one primary status plus every individual finding with its source.

A reference that is not in PubMed is never called fabricated: books, guidelines,
reports, software and many journals are legitimately outside PubMed.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .doicheck import doi_identity
from .pubmed import _norm_doi, _years_match
from .textutil import author_list, clean_text, compact, first_author_surname, first_page, similarity, split_author

STATUSES = [
    "RETRACTED", "EXPRESSION_OF_CONCERN", "PMID_MISMATCH", "DOI_MISMATCH", "DUPLICATE",
    "METADATA_ERROR", "CORRECTED", "PREPRINT", "VERIFIED_WITH_WARNINGS", "VERIFIED",
    "UNRESOLVED", "NON_PUBMED_SOURCE", "NOT_FOUND_IN_PUBMED", "CHECK_ERROR",
]
# Lower index wins when several apply to a matched reference.
_PRECEDENCE = {s: i for i, s in enumerate(STATUSES)}

STATUS_HELP = {
    "VERIFIED": "Publication identity confirmed and metadata consistent.",
    "VERIFIED_WITH_WARNINGS": "Publication confirmed, with minor differences.",
    "METADATA_ERROR": "Genuine publication, but some supplied details are wrong.",
    "DOI_MISMATCH": "The DOI in the reference belongs to a different publication.",
    "PMID_MISMATCH": "The PMID in the reference belongs to a different publication or does not exist.",
    "DUPLICATE": "The same publication appears earlier in the reference list.",
    "RETRACTED": "PubMed links this publication to a retraction notice.",
    "CORRECTED": "PubMed links this publication to a correction or erratum.",
    "EXPRESSION_OF_CONCERN": "PubMed links this publication to an expression of concern.",
    "PREPRINT": "A preprint, not a peer reviewed journal article.",
    "NOT_FOUND_IN_PUBMED": "No convincing PubMed record was found. This alone does not mean the reference is wrong.",
    "NON_PUBMED_SOURCE": "Not in PubMed, but found in another index (Crossref, OpenAlex) or looks like a source "
                         "PubMed does not cover (book, software, web page, report).",
    "UNRESOLVED": "Evidence is insufficient to decide. Needs a person to check.",
    "CHECK_ERROR": "The check could not be completed (network or service problem).",
}

PREPRINT_RE = re.compile(
    r"\b(med|bio|chem|psy|socar)Rxiv\b|\barXiv\b|Research Square|\bSSRN\b|Preprints\.org|"
    r"Authorea|\bpreprint\b|Qeios|Zenodo preprint", re.I)
PREPRINT_DOI = ("10.1101/", "10.64898/", "10.48550/", "10.21203/", "10.2139/", "10.20944/",
                "10.26434/", "10.31234/", "10.22541/", "10.32388/")
NON_PUBMED_RE = [
    (re.compile(r"github\.com|gitlab\.|bitbucket\.org|bioconductor\.org|cran\.r-project|pypi\.org|"
                r"\bR package\b|\bsoftware\b|\bversion \d", re.I), "software or code repository"),
    (re.compile(r"\bISBN\b|\bPress\b|\bPublishers?\b|\b\d+(st|nd|rd|th) ed\b|\bedition\b|\(eds?\.?\)|\bIn:\s",
                re.I), "book or book chapter"),
    (re.compile(r"\.\s*[A-Z][A-Za-z .]+:\s*[A-Z][^;:.]{2,60};\s*(19|20)\d\d\b"), "book (place: publisher; year)"),
    (re.compile(r"\bthesis\b|\bdissertation\b", re.I), "thesis"),
    (re.compile(r"\bproceedings\b|\bconference\b|\bcongress\b|\bsymposium\b|\babstract\b|\bposter\b", re.I),
     "conference material"),
    (re.compile(r"\bguideline|\bWorld Health Organization\b|\bWHO\b.*\b(report|classification)\b|"
                r"\breport\b|\bwhite paper\b|\bpolicy\b", re.I), "report or guideline"),
    (re.compile(r"\bAccessed\b|\bAvailable (from|at)\b|\bcited \d{4}\b|https?://(?!doi\.org)", re.I),
     "web page or online resource"),
]


def source_type(ref: Dict[str, Any]) -> Tuple[str, str]:
    """('preprint'|'non_pubmed'|'journal', reason) from the reference text alone."""
    text = ref.get("text", "")
    doi = _norm_doi(ref.get("doi", ""))
    if PREPRINT_RE.search(text) or doi.startswith(PREPRINT_DOI):
        return "preprint", "preprint server named in the reference or preprint DOI prefix"
    # the words that mark a book, report or abstract are searched outside the title: journal article
    # titles say 'a report from', 'guidelines', 'abstract' or 'software' too ('...: a report from the IFM')
    title = (ref.get("title") or "").strip()
    rest = text.replace(title, " ") if len(title) >= 12 and title in text else text
    for rx, label in NON_PUBMED_RE:
        if rx.search(rest):
            return "non_pubmed", label
    if missing_fields(ref):
        return "incomplete", "the reference lacks " + ", ".join(missing_fields(ref))
    return "journal", ""


def _unaccepted_pmid(ref, hits, records):
    """Findings for a written PMID on a reference without an accepted match."""
    if not ref.get("pmid_hint"):
        return [], []
    if "pmid-in-text" in hits and not hits["pmid-in-text"]:
        return [_finding("pmid_not_found", "major", f"written PMID {ref['pmid_hint']} does not exist in PubMed",
                         "PubMed efetch")], []
    o = records.get(ref["pmid_hint"], {})
    return [_finding("pmid_mismatch", "major", f"written PMID {ref['pmid_hint']} was not accepted"
                     + (f": it is '{o.get('title', '')[:90]}'" if o else ""), "RefCheck matching")], ["PMID_MISMATCH"]


def missing_fields(ref):
    """Fields a parsed reference lacks (keys absent from the dict are not judged)."""
    return [k for k in ("authors", "title", "journal", "year") if k in ref and not (ref[k] or "").strip()]


def _finding(code: str, severity: str, detail: str, source: str) -> Dict[str, str]:
    return dict(code=code, severity=severity, detail=detail, source=source)


def compare_metadata(ref: Dict[str, Any], rec: Dict[str, Any]) -> List[Dict[str, str]]:
    """Field level discrepancies between the supplied reference and a PubMed record."""
    src = f"PubMed PMID {rec['pmid']}"
    out = []
    ts = similarity(ref.get("title", ""), rec.get("title", ""))
    if ref.get("title") and ts < 0.80:
        out.append(_finding("title_differs", "major", f"title similarity {ts:.2f}", src))
    elif ref.get("title") and ts < 0.94:
        out.append(_finding("title_differs", "minor", f"title similarity {ts:.2f}", src))
    ra, ca = first_author_surname(ref.get("authors", "")), clean_text(rec.get("first_author", ""))
    ra, ca = re.sub(r"^the\s+", "", ra), re.sub(r"^the\s+", "", ca)     # 'The Cancer Genome Atlas ...'
    if ra and ca and ra.replace(" ", "") != ca.replace(" ", "") and not (len(ra) >= 5 and ra[:5] == ca[:5]):
        out.append(_finding("first_author_differs", "major",
                            f"supplied '{ref['authors'].split(',')[0]}', PubMed '{rec['first_author']}'", src))
    out += compare_authors(ref, rec, src)
    out += compare_title_words(ref, rec, src, ts)
    dy = _years_match(ref.get("year", ""), rec)
    if dy == 1:
        out.append(_finding("year_off_by_one", "minor",
                            f"supplied {ref['year']}, PubMed {rec.get('year')}"
                            + (f" (online {rec['epub_year']})" if rec.get("epub_year") else ""), src))
    elif dy is not None and dy > 1:
        out.append(_finding("year_differs", "major", f"supplied {ref['year']}, PubMed {rec.get('year')}", src))
    rj = compact(ref.get("journal", ""))
    cjs = [compact(rec.get("journal", "")), compact(rec.get("iso_journal", ""))]
    if rj and any(cjs) and rj not in cjs and not any(c and (rj in c or c in rj) for c in cjs):
        out.append(_finding("journal_differs", "major",
                            f"supplied '{ref['journal']}', PubMed '{rec.get('iso_journal') or rec.get('journal')}'", src))
    for k, sev in (("volume", "major"), ("issue", "minor")):
        a, b = clean_text(ref.get(k, "")), clean_text(rec.get(k, ""))
        if a and b and a != b:
            out.append(_finding(f"{k}_differs", sev, f"supplied {ref[k]}, PubMed {rec[k]}", src))
    rp, cp = first_page(ref.get("pages", "")), first_page(rec.get("pages", ""))
    if rp and cp and rp != cp:
        out.append(_finding("pages_differ", "major", f"supplied {ref['pages']}, PubMed {rec['pages']}", src))
    elif rp and cp:
        rl, cl = _last_page(ref.get("pages", "")), _last_page(rec.get("pages", ""))
        if rl and cl and rl != cl:
            out.append(_finding("last_page_differs", "minor", f"supplied {ref['pages']}, PubMed {rec['pages']}", src))
    return out


def _last_page(pages):
    """'1029-35' -> '1035'; '' when there is no plain numeric range ('e123', '400-416.e11')."""
    m = re.fullmatch(r"\s*([A-Za-z]?)(\d+)\s*[-\u2010-\u2014]\s*\1?(\d+)\s*", pages or "")
    if not m:
        return ""
    a, b = m.group(2), m.group(3)
    return a[: len(a) - len(b)] + b if len(b) < len(a) else b


_SPELLING = [("ae", "e"), ("our", "or"), ("ise", "ize"), ("isation", "ization"), ("yse", "yze"), ("oe", "e")]
_TITLE_STOP = set("a an and are as at by for from in into is of on or the to with without via vs versus".split())
_GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "κ": "kappa", "λ": "lambda", "μ": "mu"}


def _title_words(t, keep_stop=False):
    t = t or ""
    for g, n in _GREEK.items():
        t = t.replace(g, n)
    t = re.sub(r"<[^>]+>", " ", t)
    out = []
    for w in re.findall(r"[a-z0-9]+", clean_text(t)):
        if w in _TITLE_STOP and not keep_stop:
            continue
        for a, b in _SPELLING:
            w = w.replace(a, b)
        out.append(w)
    return out


def _rejoin(words, other):
    """Join two neighbouring words when together they make a word of the other title that neither
    is on its own ('ide' 'ntified' -> 'identified')."""
    have, out, i = set(other), [], 0
    while i < len(words):
        if i + 1 < len(words) and words[i] + words[i + 1] in have and words[i] not in have:
            out.append(words[i] + words[i + 1])
            i += 2
        else:
            out.append(words[i])
            i += 1
    return out


def compare_title_words(ref, rec, src, ts):
    """Word level title check for small changes the similarity score tolerates (one
    disease, drug, marker or population word replaced, or words exchanged)."""
    if not ref.get("title") or not rec.get("title") or ts < 0.94:
        return []
    a, b = _title_words(ref["title"]), _title_words(rec["title"])
    a, b = _rejoin(a, b), _rejoin(b, a)     # 'ide-ntified' (a word broken at a PDF line end)
    if a == b:
        fa, fb = _title_words(ref["title"], True), _title_words(rec["title"], True)
        if fa != fb and Counter(fa) == Counter(fb):
            return [_finding("title_word_order_differs", "minor", "the same words in a different order", src)]
        return []
    ca, cb = Counter(a), Counter(b)
    extra, missing = list((ca - cb).elements()), list((cb - ca).elements())
    if not extra and not missing:
        return [_finding("title_word_order_differs", "minor", "the same words in a different order", src)]
    parts = []
    if extra:
        parts.append("supplied has '" + " ".join(extra[:6]) + "'")
    if missing:
        parts.append("PubMed has '" + " ".join(missing[:6]) + "'")
    return [_finding("title_wording_differs", "minor", "; ".join(parts), src)]


def _author_positions(listed, record):
    """Where each listed author sits in the PubMed author list. Authors sharing a surname
    ("Gupta A, Gupta R", "Luo J, Luo J") are matched one to one: each takes the next unused
    PubMed author with that surname, preferring matching initials, so a shared surname is never
    mistaken for a missing author."""
    used, pos, last = set(), [], -1
    for sname, ini in listed:
        key = sname.replace(" ", "")
        same = [k for k, (rs, _) in enumerate(record) if rs.replace(" ", "") == key and k not in used]
        if not same:                                   # not in the record: reuse its first position
            same = [k for k, (rs, _) in enumerate(record) if rs.replace(" ", "") == key] or [last + 1]
            pos.append(same[0])
            continue

        def fits(k):
            ri = record[k][1]
            return bool(ini and ri and (ri.startswith(ini) or ini.startswith(ri)))
        pick = (next((k for k in same if k > last and fits(k)), None) or
                next((k for k in same if k > last), None) or
                next((k for k in same if fits(k)), None) or same[0])
        used.add(pick)
        pos.append(pick)
        last = max(last, pick)
    return pos


def _indexed_compound(listed, record):
    """A double surname that PubMed indexes under its last part, the rest folded into the
    initials: 'Gómez Casares MT' is 'Casares MTG' in PubMed. Rewrite the listed name to the
    record's form when exactly that record name is present."""
    have = {s.replace(" ", "") for s, _ in record}
    out = []
    for s, i in listed:
        parts = s.split()
        if len(parts) > 1 and s.replace(" ", "") not in have and i:
            folded = (i + "".join(w[0] for w in parts[:-1])).upper()
            hit = next(((rs, ri) for rs, ri in record if rs == parts[-1] and ri.upper() == folded), None)
            if hit:
                out.append(hit)
                continue
        out.append((s, i))
    return out


def compare_authors(ref, rec, src):
    """Initials of the first author, and the listed authors against the PubMed author list."""
    listed = [split_author(a) for a in author_list(ref.get("authors", ""))][:6]
    record = [split_author(a) for a in (rec.get("authors") or "").split(";") if a.strip()]
    if all(i for _, i in listed):
        # a group name in PubMed's list ('Precise4Q consortium') is not a person the reference
        # left out, when the reference names people only
        record = [x for x in record if x[1]] or record
    if not listed or not record:
        return []
    listed = _indexed_compound(listed, record)
    out = []
    (s0, i0), (r0, j0) = listed[0], record[0]
    if s0.replace(" ", "") == r0.replace(" ", "") and i0 and j0 and not (j0.startswith(i0) or i0.startswith(j0)):
        out.append(_finding("first_author_initials_differ", "minor",
                            f"supplied '{author_list(ref['authors'])[0]}', PubMed '{(rec.get('authors') or '').split(';')[0].strip()}'", src))
    surnames = [x[0].replace(" ", "") for x in record]
    if len(record) < len(listed):          # the record lists fewer authors than the reference
        surnames += [""] * (len(listed) - len(record))
    names = author_list(ref["authors"])
    missing = [names[k] for k, (sname, _) in enumerate(listed) if sname.replace(" ", "") not in surnames]
    later = [m for m in missing if m != names[0]]          # the first author has its own finding
    # MEDLINE capped author lists before 1996 (10 names to 1983, 25 to 1995): a shorter record
    # from then may be truncated. Since 1996 every author is listed.
    year = int(rec.get("year") or 0) if str(rec.get("year") or "").isdigit() else 0
    complete = len(record) >= len(listed) or year >= 1996
    personal = [m for m in later if split_author(m)[1]]   # surname and initials, not a group name
    if personal and complete:
        out.append(_finding("authors_not_in_record", "major",
                            "not among the PubMed authors: " + ", ".join(personal[:4]), src))
    elif not missing:
        pos = _author_positions(listed, record)
        et_al = bool(re.search(r"\bet\s*al\b", ref.get("authors", "") + " " + ref.get("text", "")[:400], re.I))
        if pos != sorted(pos):
            out.append(_finding("author_order_differs", "minor", "listed authors appear in a different order in PubMed", src))
        elif pos[0] != 0:
            out.append(_finding("authors_omitted", "major",
                                f"the reference starts at PubMed author {pos[0] + 1}; earlier authors are missing", src))
        elif pos != list(range(len(pos))):
            gap = [record[k] for k in range(pos[-1]) if k not in pos]
            out.append(_finding("authors_omitted", "major", "PubMed authors missing between the listed authors: "
                                + ", ".join(f"{a[0].title()} {a[1]}".strip() for a in gap[:4]), src))
        elif not et_al and len(names) < len(record) <= 6:
            # Vancouver lists every author up to six; the list stops early without 'et al.'
            out.append(_finding("authors_omitted", "major",
                                f"the reference lists {len(names)} of the {len(record)} PubMed authors and has no "
                                "'et al.'", src))
    return out


def publication_notices(rec: Dict[str, Any]) -> List[Dict[str, str]]:
    src = f"PubMed PMID {rec['pmid']} publication type and CommentsCorrections"
    links, types = rec.get("links") or {}, set(rec.get("pubtypes") or [])
    ids = lambda k: ", ".join(x["pmid"] or x["source"] for x in links.get(k, []))
    out = []
    if "Retracted Publication" in types or links.get("retracted_in"):
        out.append(_finding("retracted", "critical", "retraction notice " + (ids("retracted_in") or "(linked)"), src))
    if "Retraction of Publication" in types:
        out.append(_finding("is_retraction_notice", "critical",
                            "the matched record is itself a retraction notice for " + (ids("retraction_of") or "another article"), src))
    if links.get("concern_in"):
        out.append(_finding("expression_of_concern", "critical", "expression of concern " + ids("concern_in"), src))
    if links.get("erratum_in") or links.get("republished_in"):
        out.append(_finding("corrected", "info", "erratum or corrected version " +
                            ", ".join(filter(None, [ids("erratum_in"), ids("republished_in")])), src))
    if "Preprint" in types:
        out.append(_finding("preprint", "info", "PubMed publication type Preprint", src))
        if links.get("update_in"):
            out.append(_finding("published_version", "info", "peer reviewed version " + ids("update_in"), src))
    return out


def classify_reference(ref: Dict[str, Any], match: Dict[str, Any], pmid: str, decided_by: str,
                       records: Dict[str, Dict[str, Any]], duplicate_of: Optional[int] = None,
                       evidence: Optional[Dict[str, Any]] = None,
                       journal: Optional[Dict[str, Any]] = None,
                       repeated_title_of: Optional[int] = None) -> Dict[str, Any]:
    """Integrity result for one reference.

    match:      result of pubmed.match_reference
    pmid:       the PMID finally chosen ('' when none)
    decided_by: 'automatic' or 'user'
    records:    PubMed records by PMID (chosen and identifier targets)"""
    findings: List[Dict[str, str]] = []
    statuses: List[str] = []
    hits = match.get("identifier_hits") or {}
    rec = records.get(pmid) if pmid else None

    ev = evidence or {}
    doi_f, doi_st, doi_ours = doi_identity(ref, rec if (pmid and rec) else None, hits, records, ev)
    if pmid and rec:
        findings += compare_metadata(ref, rec)
        findings += publication_notices(rec)
        findings += doi_f
        statuses += doi_st
        # PMID identity
        hint = ref.get("pmid_hint")
        if hint and hint != pmid:
            target = hits.get("pmid-in-text", [])
            o = records.get(hint, {})
            if target and o:
                findings.append(_finding("pmid_mismatch", "major", f"written PMID {hint} is '{o.get('title', '')[:90]}'",
                                         "PubMed efetch of the written PMID"))
                statuses.append("PMID_MISMATCH")
            elif "pmid-in-text" in hits and not target:
                findings.append(_finding("pmid_not_found", "major", f"written PMID {hint} does not exist in PubMed; "
                                         f"the cited work is PMID {pmid}", "PubMed efetch of the written PMID"))
            else:
                findings.append(_finding("pmid_mismatch", "major", f"written PMID {hint} differs from PMID {pmid}",
                                         "RefCheck matching"))
                statuses.append("PMID_MISMATCH")
        gaps = missing_fields(ref)
        if gaps:
            findings.append(_finding("reference_incomplete", "minor", "the reference lacks " + ", ".join(gaps),
                                     "reference text"))
        codes = {f["code"] for f in findings}
        if codes & {"retracted", "is_retraction_notice"}:
            statuses.append("RETRACTED")
        if "expression_of_concern" in codes:
            statuses.append("EXPRESSION_OF_CONCERN")
        if "corrected" in codes:
            statuses.append("CORRECTED")
        if "preprint" in codes:
            statuses.append("PREPRINT")
        if any(f["severity"] == "major" for f in findings if f["code"] not in ("doi_mismatch", "pmid_mismatch")):
            statuses.append("METADATA_ERROR")
        elif any(f["severity"] == "minor" for f in findings):
            statuses.append("VERIFIED_WITH_WARNINGS")
        else:
            statuses.append("VERIFIED")
    elif pmid and not rec:
        statuses.append("CHECK_ERROR")
        findings.append(_finding("record_unavailable", "major", f"PMID {pmid} could not be retrieved", "PubMed efetch"))
    else:
        if match.get("status") == "ERROR":
            statuses.append("CHECK_ERROR")
            findings.append(_finding("check_error", "major", match.get("note", "")[:200] or "search failed",
                                     "NCBI E-utilities"))
        elif match.get("status") == "REVIEW" and decided_by != "user":
            b = match.get("best") or {}
            statuses.append("UNRESOLVED")
            findings.append(_finding("candidate_not_confirmed", "info",
                                     f"best candidate PMID {b.get('pmid')} (score {b.get('score')}); "
                                     + "; ".join(match.get("abstain") or []), "RefCheck matching"))
            kind, why = source_type(ref)
            if kind == "incomplete":
                findings.append(_finding("reference_incomplete", "major", why + "; the candidate cannot be confirmed",
                                         "reference text"))
            findings += doi_f
            statuses += doi_st
            if ref.get("pmid_hint") and ref["pmid_hint"] != b.get("pmid"):
                f3, s3 = _unaccepted_pmid(ref, hits, records)
                findings += f3
                statuses += s3
        else:
            kind, why = source_type(ref)
            if kind == "preprint":
                statuses.append("PREPRINT")
                findings.append(_finding("preprint", "info", why, "reference text"))
            elif kind == "non_pubmed":
                statuses.append("NON_PUBMED_SOURCE")
                findings.append(_finding("non_pubmed_source", "info", why, "reference text"))
            elif kind == "incomplete":
                statuses.append("UNRESOLVED")
                findings.append(_finding("reference_incomplete", "major", why + "; it could not be identified",
                                         "reference text"))
            else:
                statuses.append("NOT_FOUND_IN_PUBMED")
                ran = ", ".join(match.get("methods_run") or [])
                findings.append(_finding("not_found", "info",
                                         f"searched by {ran}" if ran else "no search was possible",
                                         "PubMed"))
                vc = match.get("volume_check") or {}
                if vc and not vc.get("volume_found"):
                    findings.append(_finding(
                        "volume_not_in_pubmed", "major",
                        f"PubMed holds {vc['journal']} records from {vc['years']} but none in volume "
                        f"{vc['volume']}; the journal, year or volume is probably wrong", "PubMed"))
            findings += doi_f
            statuses += doi_st
            f3, s3 = _unaccepted_pmid(ref, hits, records)
            findings += f3
            statuses += s3

    if duplicate_of is not None:
        findings.append(_finding("duplicate", "major", f"same publication as reference {duplicate_of}",
                                 "PMID or normalised title comparison"))
        statuses.append("DUPLICATE")
    if repeated_title_of is not None:
        findings.append(_finding("title_repeated", "minor",
                                 f"same title as reference {repeated_title_of} but other details differ (year, "
                                 "or first author and volume or pages); check whether both entries are intended",
                                 "normalised title comparison"))

    matched = bool(pmid and rec)
    if evidence or journal:
        f2, s2 = external_findings(ref, rec if matched else None, dict(ev, _written_doi_is_ours=doi_ours),
                                   journal or {}, statuses)
        findings += f2
        statuses = s2
    codes = {f["code"] for f in findings}
    if not matched and "doi_mismatch" in codes and ("not_found_in_any_source" in codes or
                                                    ("not_found" in codes and not ev.get("searched_elsewhere"))):
        where = next((f["detail"].replace("no matching work in ", "") for f in findings
                      if f["code"] == "not_found_in_any_source"), "PubMed")
        findings.append(_finding("doi_other_work_cited_not_found", "major",
                                 f"the DOI belongs to another work and the cited work was not found in {where}. "
                                 "Ask the authors for the correct identifier or a copy of the work.",
                                 "RefCheck: DOI owner comparison and search of each source"))

    strong = ("PMID_MISMATCH", "DUPLICATE", "DOI_MISMATCH", "RETRACTED", "EXPRESSION_OF_CONCERN")
    if matched or any(s in statuses for s in strong):
        primary = min(statuses, key=lambda s: _PRECEDENCE[s])
    else:
        primary = statuses[0]
    return dict(status=primary, flags=sorted(set(statuses), key=lambda s: _PRECEDENCE[s]),
                findings=findings, decided_by=decided_by, pmid=pmid, help=STATUS_HELP[primary])


_SRC_LABEL = {"publisher": "Crossref (publisher notice)", "retraction-watch": "Crossref (Retraction Watch)"}


def _rw_date(s: str):
    """Retraction Watch dates are 'M/D/YYYY 0:00'; None when unreadable."""
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    return (int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None


def external_findings(ref, rec, ev, jev, statuses):
    """Findings from secondary sources (sources.py). Returns (findings, updated statuses)."""
    out, st = [], list(statuses)
    have = {f for f in st}

    def add_status(x):
        if x not in st:
            st.append(x)

    # retractions, concerns, corrections from Crossref, OpenAlex and Retraction Watch
    xrefs = [ev.get("crossref_record")] if ev.get("crossref_record") else []
    if ev.get("crossref_written_doi") and ev.get("_written_doi_is_ours") \
            and ev["crossref_written_doi"] is not ev.get("crossref_record"):
        xrefs.append(ev["crossref_written_doi"])     # notices on a DOI of another work never apply
    if not rec and ev.get("elsewhere", {}) and ev["elsewhere"].get("source") == "Crossref":
        xrefs.append(ev["elsewhere"])
    seen = set()
    for w in xrefs:
        for u in w.get("updates", []):
            key = (u["kind"], u["doi"], u["source"])
            if key in seen or u["kind"] == "is_notice":
                continue
            seen.add(key)
            code = {"retracted": "retracted", "concern": "expression_of_concern", "corrected": "corrected"}[u["kind"]]
            sev = "info" if code == "corrected" else "critical"
            out.append(_finding(code, sev, f"{u['label']} {u['doi']}".strip(), _SRC_LABEL.get(u["source"], "Crossref")))
    oa = ev.get("openalex_record") or (ev.get("elsewhere") if (ev.get("elsewhere") or {}).get("source") == "OpenAlex" else None)
    if oa and oa.get("is_retracted"):
        out.append(_finding("retracted", "critical", f"OpenAlex marks {oa.get('doi') or 'the work'} as retracted",
                            "OpenAlex (Retraction Watch)"))
    from .sources import RetractionWatchIndex
    rw_rows = ev.get("retraction_watch") or []
    reinstated_on = max((d for d in (_rw_date(r["date"]) for r in rw_rows
                                     if RetractionWatchIndex.kind(r["nature"]) == "reinstated") if d), default=None)
    for r in rw_rows:
        k = RetractionWatchIndex.kind(r["nature"])
        how = " (matched by title and first author)" if r.get("matched_by") else ""
        src = f"Retraction Watch (local copy, record {r['record_id']}){how}"
        if k == "reinstated":
            out.append(_finding("reinstated", "info", f"{r['nature']} {r['date']}", src))
            continue
        if r.get("upgraded") and k != "retracted":
            k = "retracted"            # e.g. an expression of concern whose reason says "Updated to Retraction"
        code = {"retracted": "retracted", "concern": "expression_of_concern", "corrected": "corrected"}[k]
        detail = f"{r['nature']} {r['date']} notice {r['notice_doi'] or r['notice_pmid']}".strip()
        if r.get("reason"):
            detail += f"; reason: {r['reason'][:160]}"
        d = _rw_date(r["date"])
        if code != "corrected" and reinstated_on and d and d <= reinstated_on:
            out.append(_finding("reinstated", "info", f"{detail}; later reinstated", src))
            continue                   # the notice was superseded by a reinstatement
        out.append(_finding(code, "info" if code == "corrected" else "critical", detail, src))
    codes = {f["code"] for f in out}
    if "retracted" in codes:
        add_status("RETRACTED")
    if "expression_of_concern" in codes:
        add_status("EXPRESSION_OF_CONCERN")
    if "corrected" in codes and rec is not None:
        add_status("CORRECTED")

    wd_ours = bool(ev.get("_written_doi_is_ours"))

    # outside PubMed
    if rec is None:
        el = ev.get("elsewhere")
        if el:
            out.append(_finding("found_outside_pubmed", "info",
                                f"{el['source']}: '{el.get('title', '')[:90]}' ({el.get('journal', '')}, "
                                f"{el.get('year', '')}) DOI {el.get('doi') or 'none'}, title similarity "
                                f"{el.get('title_similarity')}", el["source"]))
            if (el.get("type") in ("posted-content", "preprint")) and "PREPRINT" not in st:
                st = ["PREPRINT" if x == "NOT_FOUND_IN_PUBMED" else x for x in st]
            else:
                st = ["NON_PUBMED_SOURCE" if x == "NOT_FOUND_IN_PUBMED" else x for x in st]
        elif ev.get("searched_elsewhere") and not wd_ours:
            names = ", ".join(["PubMed"] + [{"crossref": "Crossref", "openalex": "OpenAlex"}[x]
                                            for x in ev["searched_elsewhere"]])
            sev = "major" if "NOT_FOUND_IN_PUBMED" in st else "info"
            out.append(_finding("not_found_in_any_source", sev, f"no matching work in {names}",
                                "RefCheck search of each source"))
        elif wd_ours and ev.get("crossref_written_doi"):
            st = ["NON_PUBMED_SOURCE" if x == "NOT_FOUND_IN_PUBMED" else x for x in st]
        pre = [d for w in xrefs for d in w.get("preprint_of", [])]
        if pre:
            out.append(_finding("published_version", "info", "published version DOI " + ", ".join(pre[:3]), "Crossref"))
    else:
        cr = ev.get("crossref_record") or {}
        if cr.get("preprint_of"):
            out.append(_finding("published_version", "info", "published version DOI " + ", ".join(cr["preprint_of"][:3]),
                                "Crossref"))

    if ev.get("oa_url"):
        out.append(_finding("open_access", "info", ev["oa_url"], "Unpaywall or OpenAlex"))

    # journal
    for i in jev.get("invalid_issns") or []:
        out.append(_finding("issn_invalid", "major", f"ISSN {i} fails the ISO 3297 check digit", "ISSN check digit"))
        if rec is not None:
            add_status("METADATA_ERROR")
    nlm = jev.get("nlm")
    if nlm is not None and (ref.get("journal") or jev.get("issns")):
        if not nlm.get("found"):
            out.append(_finding("journal_not_in_nlm_catalog", "info" if rec else "minor",
                                f"'{ref.get('journal') or ', '.join(jev.get('issns', []))}' not found in the NLM Catalog",
                                "NLM Catalog"))
        elif not nlm.get("indexed"):
            out.append(_finding("journal_not_medline_indexed", "info", "journal is in the NLM Catalog but not "
                                "currently indexed for MEDLINE", "NLM Catalog"))
    if jev.get("doaj"):
        out.append(_finding("journal_in_doaj", "info", "journal listed in the Directory of Open Access Journals", "DOAJ"))
    if rec is not None and "METADATA_ERROR" in st:
        st = [x for x in st if x not in ("VERIFIED", "VERIFIED_WITH_WARNINGS")]
    return out, st


def distinctive_tokens(title: str) -> frozenset:
    """Tokens that name a specific entity: anything with a digit (TP53, IDH2, CD19, 2019)
    and, in mixed case titles, words of two or more capitals (ATM, MYC, NSCLC). Two titles
    that differ only in these tokens describe different work even when nearly identical."""
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    mixed = not ((title or "").isupper() or (title or "").islower())
    return frozenset(w.lower() for w in words
                     if any(c.isdigit() for c in w) or (mixed and len(w) >= 2 and sum(c.isupper() for c in w) >= 2))


def _same_title(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Near identical titles that also agree on distinctive tokens and in which every
    differing word has a spelling variant on the other side (leukaemia, leukemia), so a
    swapped disease or gene word ("... in melanoma" against "... in glioblastoma") is a
    different title."""
    ta, tb = clean_text(a.get("title", "")), clean_text(b.get("title", ""))
    if len(ta) <= 25 or similarity(ta, tb) < 0.95:
        return False
    if distinctive_tokens(a.get("title", "")) != distinctive_tokens(b.get("title", "")):
        return False
    wa, wb = set(re.findall(r"[a-z0-9]+", ta)), set(re.findall(r"[a-z0-9]+", tb))
    only_a, only_b = wa - wb, wb - wa
    return all(any(similarity(x, y) >= 0.75 for y in only_b) for x in only_a) and \
        all(any(similarity(y, x) >= 0.75 for x in only_a) for y in only_b)


# Duplicates whose title cannot be read: at least DUP_MIN_NUMBERS numbers in the same order, at
# least DUP_MIN_LETTERS letters of wording, and wording at least DUP_TEXT_RATIO similar.
# Varied in the sensitivity analysis of the validation.
DUP_TEXT_RATIO = 0.92
DUP_MIN_NUMBERS = 2
DUP_MIN_LETTERS = 40


def _text_key(text: str) -> Tuple[Tuple[str, ...], str]:
    """(the numbers in order, the words) of a reference without its list number and
    identifiers, for duplicates whose title cannot be read."""
    t = re.split(r"\s(?:doi\s*:|https?://|\(?PMID\s*:?|PMCID\s*:?)", text or "", maxsplit=1, flags=re.I)[0]
    t = re.sub(r"^\s*(?:\[\d+\]|\(\d+\)|\d{1,4}[.)])\s*", "", t)
    t = clean_text(t)
    return tuple(re.findall(r"\d+", t)), " ".join(re.findall(r"[a-z]+", t))


def _details_conflict(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Same title, but the first author and the volume or first page both disagree: two
    different works (templated titles), or one reference with a badly wrong citation. Either
    way not the same publication cited twice."""
    fa, fb = first_author_surname(a.get("authors", "")), first_author_surname(b.get("authors", ""))
    if not fa or not fb or fa.replace(" ", "") == fb.replace(" ", ""):
        return False
    va, vb = clean_text(a.get("volume", "")), clean_text(b.get("volume", ""))
    pa, pb = first_page(a.get("pages", "")), first_page(b.get("pages", ""))
    return bool((va and vb and va != vb) or (pa and pb and pa != pb))


def find_duplicates(refs: Iterable[Dict[str, Any]], decisions: Dict[int, Tuple[str, str]]) -> Dict[int, int]:
    """{later_ref_num: first_ref_num} for the same PMID, a near identical title and year, or,
    when no title could be read, the same numbers (year, volume, pages) in the same order and
    near identical wording (a copy with its punctuation or formatting changed).

    Titles must also share their distinctive tokens (gene symbols, numbers), so templated
    titles such as "... linked to ATM expression ..." and "... linked to TP53 expression ..."
    are not merged. Two references resolved to different PMIDs are never merged."""
    first_by_pmid: Dict[str, int] = {}
    seen: List[Dict[str, Any]] = []
    by_numbers: Dict[Tuple[str, ...], List[Tuple[int, str]]] = {}
    out: Dict[int, int] = {}
    for ref in refs:
        n = ref["num"]
        pmid = decisions.get(n, ("", ""))[0]
        if pmid:
            if pmid in first_by_pmid:
                out[n] = first_by_pmid[pmid]
                continue
            first_by_pmid[pmid] = n
        nums, words = _text_key(ref.get("text", ""))
        for r0 in seen:
            y, y0 = ref.get("year"), r0.get("year")
            if (not y or not y0 or y == y0) and _same_title(ref, r0) and not _details_conflict(ref, r0):
                out[n] = r0["num"]
                break
        else:
            if len(clean_text(ref.get("title", ""))) > 25:
                seen.append(ref)
            if len(nums) >= DUP_MIN_NUMBERS and len(words) >= DUP_MIN_LETTERS:
                for n0, w0 in by_numbers.get(nums, []):
                    p0 = decisions.get(n0, ("", ""))[0]
                    if not (pmid and p0 and pmid != p0) and \
                            SequenceMatcher(None, words, w0).ratio() >= DUP_TEXT_RATIO:
                        out[n] = n0
                        break
        if n not in out and len(nums) >= DUP_MIN_NUMBERS and len(words) >= DUP_MIN_LETTERS:
            by_numbers.setdefault(nums, []).append((n, words))
    return out


def find_repeated_titles(refs: Iterable[Dict[str, Any]], decisions: Dict[int, Tuple[str, str]],
                         duplicates: Dict[int, int]) -> Dict[int, int]:
    """{later_ref_num: earlier_ref_num} where the same title is cited again with a different
    year, or with a different first author and volume or pages, and the two are not resolved
    to one PMID. Either could be correct (a later version, a reprint, an error), so this is a
    minor prompt to check, not a duplicate."""
    refs = list(refs)
    out: Dict[int, int] = {}
    for i, ref in enumerate(refs):
        n = ref["num"]
        if n in duplicates:
            continue
        for r0 in refs[:i]:
            p, p0 = decisions.get(n, ("", ""))[0], decisions.get(r0["num"], ("", ""))[0]
            if p and p0 and p != p0:
                continue          # both resolved, to different records: two real works
            other_year = ref.get("year") and r0.get("year") and ref["year"] != r0["year"]
            if (other_year or _details_conflict(ref, r0)) and _same_title(ref, r0):
                out[n] = r0["num"]
                break
    return out


def classify_all(refs, results, decisions, records, reviewed=None, evidence=None,
                 journals=None) -> Dict[int, Dict[str, Any]]:
    """evidence, journals: from sources.gather (optional secondary sources)."""
    reviewed, evidence, journals = reviewed or {}, evidence or {}, journals or {}
    refs = list(refs)
    dups = find_duplicates(refs, decisions)
    repeats = find_repeated_titles(refs, decisions, dups)
    out = {}
    for ref in refs:
        n = ref["num"]
        pmid, st = decisions.get(n, ("", "NO_MATCH"))
        by = "user" if (st == "MANUAL" or reviewed.get(n)) else "automatic"
        ev = evidence.get(n) or {}
        out[n] = classify_reference(ref, results.get(n, {}), pmid, by, records, dups.get(n),
                                    ev, journals.get(ev.get("journal_key", "")),
                                    repeats.get(n))
    return out


def summarise(integrity: Dict[int, Dict[str, Any]]) -> Dict[str, int]:
    counts = {s: 0 for s in STATUSES}
    for r in integrity.values():
        counts[r["status"]] += 1
    return {k: v for k, v in counts.items() if v}
