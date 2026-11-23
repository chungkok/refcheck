"""Verify parsed references against PubMed (NCBI E-utilities).

Search order for each reference (stops as soon as a confident match appears):
    1. PMID written in the reference text
    2. DOI  ("10.xxxx/..."[aid])
    3. ECitMatch: journal | year | volume | first page | first author
    4. Fielded searches: journal+volume+page, author+year+page
    5. Title searches (exact phrase, then every word tagged [Title])

Each candidate is fetched and given an *evidence score* from title, first author,
year (print or online first), journal, volume, first page, DOI and PMID. The score
is not a probability. ``decide`` turns it into AUTO_MATCH, REVIEW or NO_MATCH and
abstains (REVIEW) when the evidence conflicts. See docs/ALGORITHM_SPECIFICATION.md.

Only search terms built from parsed bibliographic fields are sent to NCBI. Every
request is recorded in ``client.audit`` (see netaudit.py).
"""

from __future__ import annotations

import logging
import re
import time
from . import safexml as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .config import Settings, load_settings
from .netaudit import DiskCache, NetworkAudit
from .textutil import (clean_text, compact, extract_year, first_author_surname,
                       first_page, similarity, surname_at_start, title_in_text, token_overlap)

log = logging.getLogger("refcheck.pubmed")

NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
TOOL = "refcheck"

# Kept for backward compatibility; the live values come from Settings.
AUTO_SCORE = 86
REVIEW_SCORE = 70

STOPWORDS = {"the", "a", "an", "of", "and", "in", "for", "to", "with", "on", "from",
             "using", "based", "during", "into", "by", "as", "is", "are", "at", "or",
             "its", "their", "versus", "vs", "not", "can", "may"}
# Words PubMed may spell differently from the printed reference.
VARIANT = {"tumours", "tumour", "tumors", "tumor", "paediatric", "pediatric", "centre",
           "center", "behaviour", "behavior", "organisation", "organization",
           "oesophageal", "esophageal", "haematological", "hematological",
           "leukaemia", "leukemia", "haematopoietic", "hematopoietic", "anaemia", "anemia"}

# Publication types that are notices about another article, not the article.
# Notices are not citable work: a matched notice is never accepted automatically, and a notice
# is not a rival to the article it concerns.
NOTICE_TYPES = {"Published Erratum", "Retraction of Publication", "Expression of Concern", "Retraction Notice"}
# A comment (a letter or editorial about another article) IS citable, so it may be matched; it is
# only kept from counting as a rival. Found by the first live run: PubMed types a correspondence
# letter in Leukemia (PMID 23411466) as Letter and Comment, and it was never accepted.
RIVAL_EXEMPT_TYPES = NOTICE_TYPES | {"Comment"}


# ------------------------------------------------------------------ errors ---

class NCBIError(RuntimeError):
    """Base class. ``kind`` is a short machine readable label."""
    kind = "error"


class NCBITimeout(NCBIError):
    kind = "timeout"


class NCBIConnectionError(NCBIError):
    kind = "connection"


class NCBIRateLimited(NCBIError):
    kind = "rate_limited"


class NCBIServerError(NCBIError):
    kind = "server_error"


class NCBIBadResponse(NCBIError):
    kind = "bad_response"


class NCBIOfflineMiss(NCBIError):
    kind = "offline_cache_miss"


# ------------------------------------------------------------------ client ---

class PubMedClient:
    """Rate limited, cached, audited E-utilities client.

    Tests replace ``_fetch`` (the only method that touches the network)."""

    def __init__(self, email: str, api_key: Optional[str] = None,
                 log: Optional[Callable[[str], None]] = None, timeout: float = 30,
                 settings: Optional[Settings] = None, cache: Optional[DiskCache] = None,
                 offline: Optional[bool] = None):
        if not email or "@" not in email:
            raise ValueError("NCBI asks every tool to send a contact email. Please provide one.")
        self.settings = settings or load_settings()
        self.email, self.api_key = email.strip(), (api_key or "").strip() or None
        self.delay = 0.11 if self.api_key else 0.35   # 10 vs 3 requests per second
        self.log = log or (lambda msg: None)
        self.timeout = timeout
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"{TOOL}/2.0 ({self.email})"
        self.memo: Dict[Tuple, str] = {}      # in session deduplication
        self.cache = cache
        if self.cache is None and self.settings.cache_path:
            try:
                self.cache = DiskCache(self.settings.cache_path, self.settings.cache_ttl_days)
            except Exception as e:  # unwritable home folder: run without disk cache
                logging.getLogger("refcheck").warning("disk cache disabled: %s", type(e).__name__)
                self.cache = None
        self.offline = self.settings.offline if offline is None else offline
        self.audit = NetworkAudit()

    # ---------------------------------------------------------------- transport
    def _fetch(self, endpoint: str, params: Dict[str, Any]) -> Tuple[int, str]:
        method = self.session.post if endpoint == "efetch.fcgi" else self.session.get
        kw = {"data": params} if method == self.session.post else {"params": params}
        r = method(NCBI + endpoint, timeout=self.timeout, **kw)
        return r.status_code, r.text

    def get(self, endpoint: str, params: Dict[str, Any], retries: int = 4) -> str:
        params = dict(params, tool=TOOL, email=self.email)
        if self.api_key:
            params["api_key"] = self.api_key
        key = (endpoint, tuple(sorted((k, str(v)) for k, v in params.items()
                                      if k not in ("email", "api_key"))))
        if key in self.memo:
            return self.memo[key]
        if self.cache is not None:
            hit = self.cache.get(endpoint, params, ignore_ttl=self.offline)
            if hit is not None:
                self.audit.add(endpoint, params, True, "cached", len(hit))
                self.memo[key] = hit
                return hit
        if self.offline:
            self.audit.add(endpoint, params, True, NCBIOfflineMiss.kind)
            raise NCBIOfflineMiss(f"{endpoint}: offline mode and no cached answer")

        last_exc: Optional[NCBIError] = None
        for attempt in range(retries):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            t0 = time.time()
            try:
                status, text = self._fetch(endpoint, params)
            except requests.Timeout as e:
                last_exc = NCBITimeout(f"{endpoint}: no answer within {self.timeout} s")
            except requests.ConnectionError as e:
                last_exc = NCBIConnectionError(f"{endpoint}: cannot reach NCBI ({type(e).__name__})")
            except requests.RequestException as e:
                last_exc = NCBIError(f"{endpoint}: {type(e).__name__}")
            else:
                self._last = time.time()
                if status == 429:
                    last_exc = NCBIRateLimited(f"{endpoint}: rate limited by NCBI (HTTP 429)")
                elif status >= 500:
                    last_exc = NCBIServerError(f"{endpoint}: NCBI server error (HTTP {status})")
                elif status >= 400:
                    self.audit.add(endpoint, params, False, NCBIBadResponse.kind, len(text), time.time() - t0)
                    raise NCBIBadResponse(f"{endpoint}: request rejected (HTTP {status})")
                elif endpoint != "ecitmatch.cgi" and not text.lstrip().startswith("<"):
                    last_exc = NCBIBadResponse(f"{endpoint}: unexpected non XML answer")
                else:
                    self.audit.add(endpoint, params, False, "ok", len(text), time.time() - t0)
                    self.memo[key] = text
                    if self.cache is not None:
                        try:
                            self.cache.put(endpoint, params, text)
                        except Exception as e:
                            log.warning("cache write failed: %s", type(e).__name__)
                    return text
            self._last = time.time()
            self.audit.add(endpoint, params, False, last_exc.kind, 0, time.time() - t0)
            log.info("NCBI %s attempt %d failed: %s", endpoint, attempt + 1, last_exc.kind)
            if attempt < retries - 1:
                time.sleep(1.5 * 2 ** attempt)
        raise last_exc or NCBIError(f"{endpoint}: failed")

    # ---------------------------------------------------------------- calls --
    def esearch(self, term: str, retmax: int = 10, db: str = "pubmed") -> List[str]:
        params = {"db": db, "term": term, "retmax": retmax, "retmode": "xml"}
        if db == "pubmed":
            params["sort"] = "relevance"
        text = self.get("esearch.fcgi", params)
        try:
            root = ET.fromstring(text)
        except (ET.ParseError, ET.UnsafeXML) as e:
            raise NCBIBadResponse(f"esearch: unreadable XML ({e})") from e
        ids = [x.text for x in root.findall("./IdList/Id") if x.text]
        self.log(f"esearch hits={root.findtext('Count', '?')}  {term}")
        for tag in ("PhraseNotFound", "QuotedPhraseNotFound", "FieldNotFound"):
            for x in root.iter(tag):
                if x.text:
                    self.log(f"    {tag}: {x.text}")
        return ids

    def ecitmatch(self, journal: str, year: str, volume: str, page: str, author: str) -> List[str]:
        if not (journal and year and page):
            return []
        bdata = "|".join([journal, year, volume or "", page, author or "", "k", ""])
        text = self.get("ecitmatch.cgi", {"db": "pubmed", "retmode": "xml", "bdata": bdata})
        out = [p.strip() for ln in text.splitlines() for p in [ln.split("|")[-1]] if p.strip().isdigit()]
        self.log(f"ecitmatch {bdata} -> {out or 'none'}")
        return out

    def efetch(self, pmids: List[str]) -> List[Dict[str, Any]]:
        pmids = [p for p in dict.fromkeys(pmids) if p]
        out: List[Dict[str, Any]] = []
        for i in range(0, len(pmids), 150):      # batched
            xml = self.get("efetch.fcgi", {"db": "pubmed", "id": ",".join(pmids[i:i + 150]), "retmode": "xml"})
            try:
                out.extend(parse_pubmed_xml(xml))
            except (ET.ParseError, ET.UnsafeXML) as e:
                raise NCBIBadResponse(f"efetch: unreadable XML ({e})") from e
        return out


# ------------------------------------------------------------- XML parse ----

def _txt(el, path, default=""):
    x = el.find(path)
    return "".join(x.itertext()).strip() if x is not None else default


# PubMed CommentsCorrections RefType values that matter for integrity.
_LINKS = {
    "RetractionIn": "retracted_in", "RetractionOf": "retraction_of",
    "ErratumIn": "erratum_in", "ErratumFor": "erratum_for",
    "ExpressionOfConcernIn": "concern_in", "ExpressionOfConcernFor": "concern_for",
    "CorrectedandRepublishedIn": "republished_in", "CorrectedandRepublishedFrom": "republished_from",
    "UpdateIn": "update_in", "UpdateOf": "update_of",
}


def _surname(author: str) -> str:
    """'Mahon FX' -> 'Mahon'; a collective name ('Cancer Genome Atlas Research Network') is kept whole."""
    parts = author.rsplit(" ", 1)
    if len(parts) == 2 and re.fullmatch(r"[A-Z]{1,4}(?:-[A-Za-z])?", parts[1]):
        return parts[0]
    return author


def parse_pubmed_xml(xml_text: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_text)
    recs = []
    for art in root.findall(".//PubmedArticle"):
        mc = art.find("MedlineCitation")
        a = mc.find("Article")
        authors, full = [], []
        for au in a.findall("AuthorList/Author"):
            last, fore, ini = _txt(au, "LastName"), _txt(au, "ForeName"), _txt(au, "Initials")
            coll = _txt(au, "CollectiveName")
            if last:
                authors.append(f"{last} {ini}".strip())
                full.append(f"{last}, {fore or ini}".strip(", "))
            elif coll:
                authors.append(coll)
                full.append(coll)
        pd = a.find("Journal/JournalIssue/PubDate")
        year = _txt(pd, "Year") if pd is not None else ""
        if not year and pd is not None:
            year = extract_year(_txt(pd, "MedlineDate"))
        epub_year = ""
        for ad in a.findall("ArticleDate"):
            if ad.get("DateType", "Electronic") == "Electronic":
                epub_year = _txt(ad, "Year")
        ids = {i.get("IdType", "").lower(): (i.text or "").strip()
               for i in art.findall("PubmedData/ArticleIdList/ArticleId")}
        doi = ids.get("doi", "") or next(
            (e.text for e in a.findall("ELocationID") if e.get("EIdType") == "doi" and e.text), "")
        abstract = "\n".join(
            ((t.get("Label") + ": ") if t.get("Label") else "") + "".join(t.itertext()).strip()
            for t in a.findall("Abstract/AbstractText"))
        pages = _txt(a, "Pagination/MedlinePgn") or next(
            (e.text for e in a.findall("ELocationID") if e.get("EIdType") == "pii" and e.text), "")
        links: Dict[str, List[Dict[str, str]]] = {}
        for cc in mc.findall("CommentsCorrectionsList/CommentsCorrections"):
            name = _LINKS.get(cc.get("RefType", ""))
            if name:
                links.setdefault(name, []).append(
                    dict(pmid=_txt(cc, "PMID"), source=_txt(cc, "RefSource")))
        pubtypes = [p.text for p in a.findall("PublicationTypeList/PublicationType") if p.text]
        issns = [i.text.strip().upper() for i in a.findall("Journal/ISSN") if i.text]
        link = _txt(mc, "MedlineJournalInfo/ISSNLinking").upper()
        if link and link not in issns:
            issns.append(link)
        recs.append(dict(
            pmid=_txt(mc, "PMID"), pmcid=ids.get("pmc", ""),
            title=_txt(a, "ArticleTitle").rstrip("."),
            journal=_txt(a, "Journal/Title"), iso_journal=_txt(a, "Journal/ISOAbbreviation"),
            year=year, epub_year=epub_year, volume=_txt(a, "Journal/JournalIssue/Volume"),
            issue=_txt(a, "Journal/JournalIssue/Issue"), pages=pages,
            authors="; ".join(authors), authors_full=full,
            first_author=_surname(authors[0]) if authors else "",
            doi=doi, abstract=abstract,
            keywords=[k.text for k in mc.findall("KeywordList/Keyword") if k.text],
            pubtypes=pubtypes, links=links, issn=issns,
            nlm_id=_txt(mc, "MedlineJournalInfo/NlmUniqueID"),
            language=_txt(a, "Language"),
        ))
    return recs


# ---------------------------------------------------------------- scoring ---

def _norm_doi(d: str) -> str:
    """Lower case, strip resolver prefixes, 'doi:' and trailing punctuation."""
    d = (d or "").strip().lower()
    d = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.rstrip(".,;)]")


def _years_match(ref_year: str, cand: Dict[str, Any], online_first: bool = True) -> Optional[int]:
    """Smallest year difference against print or (if allowed) online first year."""
    if not ref_year:
        return None
    ys = [y for y in (cand.get("year"), cand.get("epub_year") if online_first else "") if y]
    if not ys:
        return None
    return min(abs(int(ref_year) - int(y)) for y in ys)


def title_similarity_any_style(ref: Dict[str, Any], cand: Dict[str, Any]) -> float:
    """Title evidence: the parsed title against the record's, or the record's title found in
    the raw reference text, whichever is stronger. The second makes the evidence independent of
    the citation style: a misread Nature, IEEE or Harvard reference still shows its title."""
    ts = similarity(ref.get("title", ""), cand.get("title", ""))
    if ts < 0.98 and ref.get("text"):
        ts = max(ts, title_in_text(cand.get("title", ""), ref["text"]))
    if ts < 0.88:
        # a title cut short: the words given are the start of the record's title
        a = re.findall(r"[a-z0-9]+", clean_text(ref.get("title", "")))
        b = re.findall(r"[a-z0-9]+", clean_text(cand.get("title", "")))
        if len(a) >= 5 and len(a) >= 0.4 * len(b) and b[: len(a)] == a:
            ts = 0.88
    return ts


def first_author_any_style(ref: Dict[str, Any], cand: Dict[str, Any]) -> str:
    """exact, prefix5, differs or missing: from the parsed author list, or, when that does not
    agree, from the record's surname being the first name in the raw text."""
    ra, ca = first_author_surname(ref.get("authors", "")), clean_text(cand.get("first_author", ""))
    ra, ca = re.sub(r"^the\s+", "", ra), re.sub(r"^the\s+", "", ca)    # 'The Cancer Genome Atlas ...'
    if ca and ra and (ra == ca or ra.replace(" ", "") == ca.replace(" ", "")):
        return "exact"
    if ca and ref.get("text") and surname_at_start(cand.get("first_author", ""), ref["text"]):
        return "exact"
    if not ra or not ca:
        return "missing"
    if len(ra) >= 5 and ra[:5] == ca[:5]:
        return "prefix5"
    if min(len(ra), len(ca)) >= 4 and _one_edit(ra, ca):
        return "prefix5"        # a misspelt surname ('Slmaon' for 'Slamon'): close, not a different person
    second = [a for a in (cand.get("authors") or "").split("; ") if a][1:2]
    if second and clean_text(_surname(second[0])) == ra:
        return "prefix5"        # the first author left out: the list starts with PubMed's second author
    return "differs"


def _one_edit(a: str, b: str) -> bool:
    """True when a and b differ by one substitution, insertion, deletion or swap of neighbours."""
    if a == b or abs(len(a) - len(b)) > 1:
        return a == b
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    s, l = (a, b) if len(a) < len(b) else (b, a)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def score_candidate(ref: Dict[str, Any], cand: Dict[str, Any], settings: Optional[Settings] = None):
    """Return (score, title_similarity, token_overlap, reasons)."""
    online_first = not (settings and "online-first" in settings.disabled)
    reasons, score = [], 0
    ts = title_similarity_any_style(ref, cand)
    to = token_overlap(ref["title"], cand["title"])
    doi_hit = bool(ref.get("doi") and cand.get("doi") and _norm_doi(ref["doi"]) == _norm_doi(cand["doi"]))
    pmid_hit = bool(ref.get("pmid_hint") and ref["pmid_hint"] == cand["pmid"])

    untitled = not (ref.get("title") or "").strip()
    if untitled:
        # no title to compare ('Mahon FX, et al. Lancet Oncol. 2010;11:1029-35.', a DOI or a PMID
        # alone): the base is set so that only full agreement of what the reference does give
        # reaches the automatic threshold: first author, journal, year, volume and first page
        # (46 + 15 + 10 + 8 + 3 + 5), or the first author and the DOI (46 + 15 + 25), or a PMID
        # written alone (56 + 30). Anything less goes to review.
        alone = not (ref.get("authors") or ref.get("journal"))
        score, r = (56 if alone and pmid_hit else 46), "no-title"
    elif ts >= 0.98:
        score, r = 60, "title>=0.98"
    elif ts >= 0.94:
        score, r = 54, "title>=0.94"
    elif ts >= 0.88:
        score, r = 46, "title>=0.88"
    elif ts >= 0.80:
        score, r = 34, "title>=0.80"
    elif to >= 0.75:
        score, r = 30, "title-words>=0.75"
    elif to >= 0.60:
        score, r = 20, "title-words>=0.60"
    elif doi_hit or pmid_hit:
        score, r = 10, "title-differs"
    else:
        return 0, ts, to, ["title-too-different"]
    reasons.append(r)

    fa = first_author_any_style(ref, cand)
    if fa == "exact":
        score += 15; reasons.append("first-author")
    elif fa == "prefix5":
        score += 8; reasons.append("author-partial")
    elif fa == "differs":
        reasons.append("author-differs")

    dy = _years_match(ref["year"], cand, online_first)
    if dy is not None:
        if dy == 0:
            score += 10
            reasons.append("year" if ref["year"] == cand.get("year") else "year(online-first)")
        elif dy == 1:
            score += 2; reasons.append("year+-1")
        else:
            score -= 12; reasons.append("year-mismatch")

    rj = compact(ref["journal"])
    cjs = [compact(cand["journal"]), compact(cand["iso_journal"])]
    if rj and any(cjs):
        if rj in cjs:
            score += 8; reasons.append("journal")
        elif any(c and (rj in c or c in rj) for c in cjs):
            score += 5; reasons.append("journal-partial")
        else:
            reasons.append("journal-differs")

    if ref["volume"] and cand["volume"] and clean_text(ref["volume"]) == clean_text(cand["volume"]):
        score += 3; reasons.append("volume")
    rp, cp = first_page(ref["pages"]), first_page(cand["pages"])
    if rp and cp and rp == cp:
        score += 5; reasons.append("first-page")
    if doi_hit:
        score += 25; reasons.append("doi")
    if pmid_hit:
        score += 30; reasons.append("pmid-in-text")
    return score, ts, to, reasons


def _is_notice(c: Dict[str, Any]) -> bool:
    types = set(c.get("pubtypes") or [])
    notice = types & NOTICE_TYPES
    if notice == {"Retraction Notice"} and "Retracted Publication" in types:
        # an Elsevier withdrawal: one PubMed record is both the withdrawn article and its
        # notice ('WITHDRAWN: Title'), so there is no separate article to link instead
        return False
    return bool(notice)


def decide(best: Optional[Dict[str, Any]], second: Optional[Dict[str, Any]],
           settings: Optional[Settings] = None) -> Tuple[str, List[str]]:
    """Return (status, abstention_reasons).

    AUTO_MATCH needs the score threshold, a clear lead over the runner up, and no
    conflicting evidence. When evidence conflicts the result is REVIEW: an
    unresolved reference is preferred over a wrong automatic match."""
    s = settings or load_settings()
    if best is None:
        return "NO_MATCH", []
    why = set((best.get("match_reasons") or "").split(";"))
    ident = bool(why & {"doi", "pmid-in-text"})
    abstain = []
    if "probability" in best:              # evidence scoring: thresholds are probabilities
        from .evidence import model_for
        m = model_for(s)
        is_auto, is_review = best["probability"] >= m.auto_probability, best["probability"] >= m.review_probability
        gap, gap_unit = m.min_gap_bits, "bits (log2 odds)"
    else:
        is_auto, is_review = best["score"] >= s.auto_score, best["score"] >= s.review_score
        gap, gap_unit = s.min_gap, "points"
    if is_auto:
        rival = second if (second and ("notice-rival" in s.disabled or
                                       not set(second.get("pubtypes") or []) & RIVAL_EXEMPT_TYPES)) else None
        if rival and best["score"] - rival["score"] < gap:
            abstain.append(f"runner up PMID {rival['pmid']} is within {gap:g} {gap_unit}")
        if not ident and "abstention" not in s.disabled:
            if "author-differs" in why:
                abstain.append("first author differs")
            if "year-mismatch" in why:
                abstain.append("year differs by more than one")
            if best.get("title_similarity", 1) < 0.80 and "no-title" not in why:
                abstain.append("title similarity below 0.80")
        elif "abstention" not in s.disabled and "author-differs" in why and "journal-differs" in why:
            # an identifier points here, but two independent fields describe another paper
            abstain.append("identifier supports this record but first author and journal both differ")
        lv = best.get("levels") or {}
        if "abstention" not in s.disabled and lv.get("title") == "low" and "no-title" not in why \
                and not (lv.get("doi") == "agree" and lv.get("pmid") == "agree") \
                and not any(x.startswith("title similarity below") for x in abstain):
            # journal, volume, page and DOI come from one record and are not independent
            # evidence: a title that describes another work needs a person to decide, unless
            # the author typed both identifiers of this record (then the title was usually
            # misread from an unusual layout)
            abstain.append("title differs from the record although other fields agree")
        if _is_notice(best):
            abstain.append("best candidate is a notice (erratum, retraction or expression of concern)")
        if not abstain:
            return "AUTO_MATCH", []
        return "REVIEW", abstain
    if is_review:
        return "REVIEW", ["score below the automatic threshold"]
    return "NO_MATCH", []


def classify(best, second, settings: Optional[Settings] = None) -> str:
    """Backward compatible wrapper around decide()."""
    return decide(best, second, settings)[0]


# -------------------------------------------------------------- searching ---

def title_terms(title: str, n: int = 14) -> List[str]:
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    keep = [w for w in words if w.lower() not in STOPWORDS and w.lower() not in VARIANT
            and not re.search(r"\d", w) and len(w) >= 3]
    return keep[:n]


def _plan(ref):
    """Yield (method, kind, payload) in order of decreasing specificity."""
    au = first_author_surname(ref["authors"])
    fp = first_page(ref["pages"])
    j, y, v, t = ref["journal"].strip(" ."), ref["year"], ref["volume"], ref["title"]
    if ref.get("pmid_hint"):
        yield "pmid-in-text", "ids", [ref["pmid_hint"]]
    if ref.get("doi"):
        yield "doi", "term", f'"{_norm_doi(ref["doi"])}"[aid]'
    if j and y and fp:
        yield "citmatch", "cit", (j, y, v, fp, au)
    if j and y and v and fp:
        yield "journal-vol-page", "term", f'"{j}"[ta] AND {y}[dp] AND {v}[vi] AND {fp}[pg]'
    if au and y and fp:
        yield "author-year-page", "term", f'{au}[1au] AND {y}[dp] AND {fp}[pg]'
    if t:
        phrase = re.sub(r'["\[\]]', " ", t).strip()
        yield "title-phrase", "term", f'"{phrase}"[Title]'
    words = title_terms(t)
    if words:
        tagged = " AND ".join(f"{w}[Title]" for w in words)
        if au:
            yield "title+author", "term", f"{tagged} AND {au}[Author]"
        if y:
            yield "title+year", "term", f"{tagged} AND {int(y) - 1}:{int(y) + 1}[dp]"
        yield "title-words", "term", tagged
        if len(words) > 6:   # looser: the 6 rarest looking (longest) words
            loose = sorted(words, key=len, reverse=True)[:6]
            yield "title-words-loose", "term", " AND ".join(f"{w}[Title]" for w in loose)
    tw = text_terms(ref.get("text", ""))
    if len(tw) >= 4:
        # style independent: the most distinctive words of the whole reference, in any field, so a
        # reference whose title, authors or journal were misread can still be found
        yield "text-words", "term", " AND ".join(tw)


_TEXT_STOP = {"journal", "medicine", "research", "international", "american", "society", "clinical", "volume",
              "pages", "press", "university", "available", "accessed", "online", "retrieved", "between", "during",
              "through", "within", "without", "versus", "among", "after", "before", "their", "these", "those",
              "which", "where", "while", "using", "based", "study", "analysis", "patients"}


def text_terms(text: str, n: int = 6) -> List[str]:
    """The n longest distinct words of a raw reference (letters only, at least 6 long, not
    common journal or title filler), for a search that does not rely on the parse."""
    raw = re.sub(r"(?:https?://|doi\s*:?\s*)\S+", " ", text or "", flags=re.I)
    seen, words = set(), []
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{5,}", raw):
        k = w.lower().strip("-")
        if k in seen or k in _TEXT_STOP or "-" in k:
            continue
        seen.add(k)
        words.append(k)
    return sorted(words, key=len, reverse=True)[:n]


def match_reference(client: PubMedClient, ref: Dict[str, Any], max_candidates: int = 40,
                    settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Return a result dict:
        status, best, second, note, abstain (reasons), identifier_hits
        {'doi': [pmids], 'pmid-in-text': [pmids]}, error_kinds, methods_run."""
    s = settings or getattr(client, "settings", None) or load_settings()
    seen, found, methods = set(), {}, {}
    id_hits: Dict[str, List[str]] = {}

    from .evidence import compare_fields, model_for
    model = model_for(s)
    online_first = "online-first" not in s.disabled
    levels: Dict[str, Dict[str, str]] = {}

    def rank():
        scored = []
        for c in found.values():
            sc, ts, to, why = score_candidate(ref, c, s)
            if c["pmid"] not in levels:
                levels[c["pmid"]] = compare_fields(ref, c, online_first)
            lv = levels[c["pmid"]]
            extra = dict(points=sc, levels=lv)
            if model is not None:
                lo = model.log2_odds(lv)
                extra.update(score=round(lo, 2), probability=round(model.probability(lo), 4),
                             weights_explained=model.explain(lv))
                keep = True                 # the weights judge every candidate, no title cut off
            else:
                extra.update(score=sc)
                keep = sc > 0
            if keep:
                scored.append(dict(c, title_similarity=round(ts, 3), token_overlap=round(to, 3),
                                   match_reasons=";".join(why),
                                   search_methods=";".join(methods.get(c["pmid"], [])), **extra))
        scored.sort(key=lambda x: (x["score"], x["title_similarity"]), reverse=True)
        return scored

    locator = bool(ref.get("journal") and ref.get("year") and first_page(ref.get("pages", "")))
    if not (ref.get("title") or ref.get("doi") or ref.get("pmid_hint") or locator
            or len(text_terms(ref.get("text", ""))) >= 4):
        return dict(status="NO_MATCH", best=None, second=None, note="no title parsed",
                    abstain=[], identifier_hits={}, error_kinds=[], methods_run=[])

    errors, kinds, run = [], [], []
    for method, kind, payload in _plan(ref):
        if method in s.disabled:
            continue
        run.append(method)
        try:
            if kind == "ids":
                pmids = payload
            elif kind == "cit":
                pmids = client.ecitmatch(*payload)
            else:
                pmids = client.esearch(payload, retmax=10)
        except NCBIError as e:  # keep going with the other strategies
            errors.append(f"{method}: {e}")
            kinds.append(e.kind)
            continue
        if method in ("doi", "pmid-in-text"):
            id_hits[method] = list(pmids)
        new = [p for p in pmids if p not in seen][: max(0, max_candidates - len(seen))]
        for p in pmids:
            methods.setdefault(p, []).append(method)
        if new:
            seen.update(new)
            try:
                for c in client.efetch(new):
                    found[c["pmid"]] = c
            except NCBIError as e:
                errors.append(f"efetch: {e}")
                kinds.append(e.kind)
            if method == "pmid-in-text" and not any(p in found for p in new):
                id_hits[method] = []          # the written PMID does not exist
        scored = rank()
        if ("early-stop" not in s.disabled and scored
                and decide(scored[0], scored[1] if len(scored) > 1 else None, s)[0] == "AUTO_MATCH"):
            break   # confident; skip the remaining searches

    # A written DOI is always checked: when an earlier search already settled the match
    # it can still belong to another paper (one query, its owner fetched later).
    if ref.get("doi") and "doi" not in run and "doi" not in s.disabled:
        run.append("doi")
        try:
            id_hits["doi"] = list(client.esearch(f'"{_norm_doi(ref["doi"])}"[aid]', retmax=5))
        except NCBIError as e:
            errors.append(f"doi: {e}")
            kinds.append(e.kind)

    scored = rank()
    best = scored[0] if scored else None
    second = scored[1] if len(scored) > 1 else None
    status, abstain = decide(best, second, s)
    if status == "NO_MATCH" and errors and not found:
        status = "ERROR"
    volume = None
    # also when PubMed offered candidates that were all rejected: a cited volume that does not
    # exist is evidence either way (found by the first live run: common surnames such as Zhou
    # return loose candidates, which skipped the check)
    if status == "NO_MATCH" and "volume-check" not in s.disabled:
        volume = check_volume(client, ref)
        if volume:
            run.append("volume-check")
    return dict(status=status, best=best, second=second, note="; ".join(errors),
                abstain=abstain, identifier_hits=id_hits, error_kinds=sorted(set(kinds)),
                methods_run=run, candidates_seen=len(found), volume_check=volume,
                scoring="evidence" if model is not None else "points",
                candidates=[dict(pmid=p, levels=lv, points=points_of(ref, found[p], s))
                            for p, lv in levels.items()])


def points_of(ref, cand, settings=None) -> int:
    return score_candidate(ref, cand, settings)[0]


def check_volume(client: PubMedClient, ref: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """For a reference PubMed could not find: does the cited journal volume exist around the
    cited year? Two searches: the journal in year +/- 1, then that volume in the same
    window. Returns None when the check does not apply (no journal, volume or year, a
    non numeric volume, or the journal has no PubMed records in those years, which is
    also what an unrecognised journal abbreviation looks like) or a search failed."""
    j = (ref.get("journal") or "").strip(" .")
    v, y = (ref.get("volume") or "").strip(), (ref.get("year") or "").strip()
    if not (j and v.isdigit() and re.fullmatch(r"\d{4}", y)):
        return None
    window = f"{int(y) - 1}:{int(y) + 1}[dp]"
    try:
        if not client.esearch(f'"{j}"[ta] AND {window}', retmax=1):
            return None
        hits = client.esearch(f'"{j}"[ta] AND {v}[vi] AND {window}', retmax=1)
    except NCBIError:
        return None
    return dict(journal=j, volume=v, years=f"{int(y) - 1} to {int(y) + 1}", volume_found=bool(hits))
