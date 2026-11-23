"""Does the DOI written in a reference belong to the work the reference describes?

The written DOI is looked up on its own (PubMed [aid] search, Crossref /works/{doi}),
which gives the record that owns it. That owner is compared with the claimed work:
the matched PubMed record when there is one, otherwise the fields written in the
reference. Four fields are compared (title, first author surname, year, journal) and
a mismatch needs more than a different title.

Before a mismatch is reported, three legitimate reasons for a different owner are
ruled out:
  * related versions: Crossref `relation` links (preprint and published version and
    the like) or PubMed CommentsCorrections links between the two records, or a
    preprint DOI by the same first author within two years;
  * notices: the DOI belongs to the correction, retraction or concern notice for
    the cited paper ("Correction to: <title>");
  * typos: reported as a hint beside the mismatch when the intended DOI is known and
    differs by at most two characters within the same DOI prefix.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .pubmed import _norm_doi
from .textutil import clean_text, compact, content_swapped, first_author_surname, similarity, title_normalize

PREPRINT_DOI = ("10.1101/", "10.64898/", "10.48550/", "10.21203/", "10.2139/", "10.20944/",
                "10.31219/", "10.31234/", "10.22541/", "10.36227/")
NOTICE_PREFIX = re.compile(
    r"^\s*(?:notice of\s+)?(?:retraction(?: note)?|retracted(?: article)?|withdrawn|withdrawal|correction|"
    r"corrigendum|erratum|addendum|(?:editorial\s+)?expression of concern)"
    r"(?:\s*[:.\-\u2013\u2014]\s*|\s+(?:to|for|regarding)\b\s*:?\s*)", re.I)
NOTICE_TYPES = {"Published Erratum", "Retraction of Publication", "Expression of Concern",
                "Retraction Notice", "Correction"}


# ------------------------------------------------------------- descriptions ---

def _work(title="", first_author="", years=(), journals=(), issns=(), doi="", source="", **kw):
    w = dict(title=title or "", first_author=clean_text(first_author or ""), years=[y for y in years if y],
             journals=[j for j in journals if j], issns=[i.upper() for i in issns if i], doi=_norm_doi(doi or ""),
             source=source, related_dois=set(), related_pmids=set(), is_notice=False, notice_for=set(),
             type="", pmid="")
    w.update(kw)
    return w


def claimed_from(ref: Dict[str, Any], rec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if rec:
        return from_pubmed(rec)
    from .sources import issns_in_text
    return _work(ref.get("title", ""), first_author_surname(ref.get("authors", "")), [ref.get("year", "")],
                 [ref.get("journal", "")], issns_in_text(ref.get("text", "")), ref.get("doi", ""), "reference")


def from_pubmed(rec: Dict[str, Any]) -> Dict[str, Any]:
    links = rec.get("links") or {}
    pmids = {x.get("pmid") for v in links.values() for x in v if x.get("pmid")}
    title = rec.get("title", "")
    types = set(rec.get("pubtypes") or [])
    return _work(title, rec.get("first_author", ""), [rec.get("year", ""), rec.get("epub_year", "")],
                 [rec.get("journal", ""), rec.get("iso_journal", "")], rec.get("issn") or [], rec.get("doi", ""),
                 f"PubMed PMID {rec.get('pmid', '')}", related_pmids=pmids, pmid=rec.get("pmid", ""),
                 is_notice=bool(types & NOTICE_TYPES) or bool(NOTICE_PREFIX.match(title)),
                 notice_for={x.get("pmid") for k in ("erratum_for", "retraction_of", "concern_for")
                             for x in links.get(k, []) if x.get("pmid")},
                 type="preprint" if "Preprint" in types else "")


def from_crossref(w: Dict[str, Any]) -> Dict[str, Any]:
    ups = w.get("updates") or []
    title = w.get("title", "")
    return _work(title, w.get("first_author", ""), [w.get("year", "")],
                 [w.get("journal", ""), w.get("short_journal", "")], w.get("issn") or [], w.get("doi", ""),
                 "Crossref", related_dois=set(w.get("related") or []) | set(w.get("preprint_of") or [])
                 | set(w.get("has_preprint") or []),
                 is_notice=any(u["kind"] == "is_notice" for u in ups) or bool(NOTICE_PREFIX.match(title)),
                 notice_for={u["doi"] for u in ups if u["kind"] == "is_notice"}, type=w.get("type", ""))


# --------------------------------------------------------------- comparison ---

def _years(w):
    out = []
    for y in w["years"]:
        m = re.search(r"(1[89]|20)\d\d", str(y))
        if m:
            out.append(int(m.group(0)))
    return out


# Title similarity from which a DOI owner's title agrees with the cited title (unless a content
# word or number is swapped). Varied in the sensitivity analysis of the validation.
TITLE_AGREE = 0.80


def _title_agrees(a: str, b: str) -> Optional[bool]:
    if not a or not b:
        return None
    if similarity(a, b) >= TITLE_AGREE:
        # series titles ("... leukemia: 2023 update ..." and "... myeloma: 2024 update ...") are
        # similar as strings but name different work
        return not content_swapped(a, b)
    na, nb = title_normalize(a), title_normalize(b)
    short, long_ = sorted((na, nb), key=len)
    return len(short) >= 30 and long_.startswith(short)      # subtitle dropped or added


def compare(claimed: Dict[str, Any], owner: Dict[str, Any]) -> Dict[str, Any]:
    """{'title', 'first_author', 'year', 'journal': True | False | None, 'title_similarity'}."""
    t = _title_agrees(claimed["title"], owner["title"])
    a, b = claimed["first_author"].replace(" ", ""), owner["first_author"].replace(" ", "")
    au = None if not a or not b else (a == b or (len(a) >= 5 and a[:5] == b[:5]) or a in b or b in a)
    ya, yb = _years(claimed), _years(owner)
    yr = None if not ya or not yb else min(abs(x - y) for x in ya for y in yb) <= 1
    jn = None
    if set(claimed["issns"]) & set(owner["issns"]):
        jn = True
    else:
        ca, cb = [compact(x) for x in claimed["journals"]], [compact(x) for x in owner["journals"]]
        ca, cb = [x for x in ca if x], [x for x in cb if x]
        if ca and cb:
            jn = any(x == y or x in y or y in x for x in ca for y in cb)
    return dict(title=t, first_author=au, year=yr, journal=jn,
                title_similarity=round(similarity(claimed["title"], owner["title"]), 2)
                if claimed["title"] and owner["title"] else None)


def verdict(cmp: Dict[str, Any]) -> str:
    """same | reworded | different | unclear."""
    others = [cmp[k] for k in ("first_author", "year", "journal")]
    agree, differ = sum(1 for x in others if x is True), sum(1 for x in others if x is False)
    if cmp["title"] is True:
        return "same"
    if cmp["title"] is False:
        if agree >= 2 and differ == 0 and (cmp["title_similarity"] or 0) >= 0.5:
            return "reworded"
        return "different"
    if differ >= 2:
        return "different"
    if agree >= 2 and differ == 0:
        return "same"
    return "unclear"


def _differing(cmp):
    names = {"title": "title", "first_author": "first author", "year": "year", "journal": "journal"}
    return [names[k] for k in names if cmp[k] is False]


def doi_distance(a: str, b: str) -> int:
    """Levenshtein distance (DOIs are short, so the plain algorithm is fine)."""
    a, b = _norm_doi(a), _norm_doi(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def likely_typo(written: str, intended: str) -> bool:
    w, i = _norm_doi(written), _norm_doi(intended)
    return bool(w and i and w != i and w.split("/", 1)[0] == i.split("/", 1)[0] and doi_distance(w, i) <= 2)


def _related(owner, claimed, target_dois, crossref_target):
    if owner["doi"] and owner["doi"] in claimed["related_dois"]:
        return True
    if any(d and d in owner["related_dois"] for d in target_dois):
        return True
    if crossref_target and owner["doi"] in (crossref_target.get("related_dois") or set()):
        return True
    if claimed.get("pmid") and (claimed["pmid"] in owner["related_pmids"] or
                                (owner.get("pmid") and owner["pmid"] in claimed["related_pmids"])):
        return True
    return False


def _notice_for_claimed(owner, claimed, target_dois):
    if not owner["is_notice"]:
        return False
    if any(d and d in owner["notice_for"] for d in target_dois):
        return True
    if claimed.get("pmid") and claimed["pmid"] in owner["notice_for"]:
        return True
    stripped = NOTICE_PREFIX.sub("", owner["title"]).strip(" \"'")
    return bool(stripped and _title_agrees(claimed["title"], stripped))


def _preprint_version(owner, claimed, written, target_dois, cmp):
    pre = (owner["type"] in ("posted-content", "preprint") or owner["doi"].startswith(PREPRINT_DOI)
           or _norm_doi(written).startswith(PREPRINT_DOI) or any(d.startswith(PREPRINT_DOI) for d in target_dois if d)
           or claimed["type"] == "preprint")
    if not pre or cmp["first_author"] is not True:
        return False
    ya, yb = _years(claimed), _years(owner)
    return not ya or not yb or min(abs(x - y) for x in ya for y in yb) <= 2


# ---------------------------------------------------------------- findings ---

def _f(code, severity, detail, source):
    return dict(code=code, severity=severity, detail=detail, source=source)


def _describe(o):
    bits = [x for x in (o["first_author"].title() if o["first_author"] else "",
                        (o["journals"] or [""])[-1], str((_years(o) or [""])[0])) if x]
    if o.get("pmid"):
        bits.append(f"PMID {o['pmid']}")
    return f"'{o['title'][:90]}'" + (f" ({', '.join(bits)})" if bits else "")


def doi_identity(ref: Dict[str, Any], rec: Optional[Dict[str, Any]], hits: Dict[str, List[str]],
                 records: Dict[str, Dict[str, Any]], ev: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[str], bool]:
    """Findings, statuses to add, and whether the written DOI belongs to the cited work.

    The last value is used to decide whether notices found on the written DOI apply."""
    written = _norm_doi(ref.get("doi", ""))
    if not written:
        return [], [], False
    claimed = claimed_from(ref, rec)
    target = claimed["doi"] if rec else ""
    if rec and target == written:
        return [], [], True
    elsewhere = (ev.get("elsewhere") or {}) if not rec else {}
    target_dois = [d for d in (target, _norm_doi(elsewhere.get("doi", ""))) if d]
    crossref_target = from_crossref(ev["crossref_record"]) if ev.get("crossref_record") else None

    owners = []
    pm_hits = hits.get("doi") or []
    if rec and rec.get("pmid") in pm_hits:
        return [], [], True              # PubMed files the written DOI under the cited record
    for p in pm_hits:
        if records.get(p):
            owners.append(from_pubmed(records[p]))
            break
    if ev.get("crossref_written_doi"):
        owners.append(from_crossref(ev["crossref_written_doi"]))

    out: List[Dict[str, str]] = []
    add: List[str] = []
    intended = next((d for d in target_dois if likely_typo(written, d)), "")
    if not owners:
        if ev.get("doi_registered") is False:
            out.append(_f("doi_not_registered", "major", f"DOI {ref.get('doi')} is not registered at doi.org",
                          "doi.org handle API"))
        elif rec and target:
            out.append(_f("doi_differs", "major", f"supplied DOI {ref['doi']}, PubMed DOI {rec['doi']}",
                          f"PubMed PMID {rec.get('pmid')}"))
        elif not rec and "doi" in hits and not hits["doi"]:
            out.append(_f("doi_not_in_pubmed", "minor", f"DOI {ref['doi']} is not in PubMed; it may still be "
                          "registered (turn on the doi.org and Crossref checks to find out)", "PubMed [aid] search"))
        if intended:
            out.append(_f("doi_typo_likely", "info", f"probably intended DOI {intended} "
                          f"({doi_distance(written, intended)} characters differ)", "DOI comparison"))
        return out, add, False

    results = [(o, compare(claimed, o)) for o in owners]
    best = None
    for o, c in results:
        v = verdict(c)
        if best is None or ["different", "unclear", "reworded", "same"].index(v) > \
                ["different", "unclear", "reworded", "same"].index(best[2]):
            best = (o, c, v)
    owner, cmp, v = best
    for o, _c in results:
        if _notice_for_claimed(o, claimed, target_dois):
            owner = o
    src = " and ".join(dict.fromkeys(o["source"].split(" PMID")[0] for o in owners))

    if _notice_for_claimed(owner, claimed, target_dois):
        out.append(_f("doi_is_notice", "minor", f"DOI {ref['doi']} is the notice {_describe(owner)}, not the "
                      "article itself" + (f"; the article DOI is {target}" if target else ""), src))
        return out, add, False
    if v == "same":
        if rec:
            out.append(_f("doi_alternative", "info", f"DOI {ref['doi']} differs from PubMed's {rec.get('doi')} but "
                          "is registered for the same work", src))
        else:
            out.append(_f("doi_confirmed", "info", f"DOI {ref['doi']} is registered for this work "
                          f"{_describe(owner)}", src))
        return out, add, True
    if v == "reworded":
        out.append(_f("doi_title_differs", "minor", f"DOI {ref['doi']} is registered for {_describe(owner)}: "
                      "title differs, first author, year and journal agree", src))
        return out, add, True
    if _related(owner, claimed, target_dois, crossref_target) or \
            _preprint_version(owner, claimed, written, target_dois, cmp):
        linked = _related(owner, claimed, target_dois, crossref_target)
        out.append(_f("doi_is_related_version", "info" if linked else "minor",
                      f"DOI {ref['doi']} is {'a linked version' if linked else 'probably another version (preprint or published)'}"
                      f" of the cited work: {_describe(owner)}", src if linked else src + " and RefCheck version rule"))
        return out, add, False
    if v == "unclear":
        out.append(_f("doi_owner_unclear", "minor", f"DOI {ref['doi']} is registered for {_describe(owner)}; "
                      "too few details to compare", src))
        return out, add, False
    diff = _differing(cmp)
    out.append(_f("doi_mismatch", "major", f"DOI {ref['doi']} is registered for {_describe(owner)}"
                  + (f"; {', '.join(diff)} differ" if diff else ""), src))
    add.append("DOI_MISMATCH")
    if intended:
        out.append(_f("doi_typo_likely", "info", f"probably intended DOI {intended} "
                      f"({doi_distance(written, intended)} characters differ)", "DOI comparison"))
    return out, add, False
