"""Verify parsed references against PubMed (NCBI E-utilities).

Search order for each reference (stops as soon as a confident match appears):
    1. PMID written in the reference text
    2. DOI  ("10.xxxx/..."[aid])
    3. ECitMatch: journal | year | volume | first page | first author
    4. Fielded searches: journal+volume+page, author+year+page
    5. Title searches (exact phrase, then every word tagged [Title])

Each candidate is fetched and scored on title, first author, year, journal,
volume, first page and DOI.
"""

import re
import time
import xml.etree.ElementTree as ET

import requests

from .textutil import (clean_text, compact, extract_year, first_author_surname,
                       first_page, similarity, token_overlap)

NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
TOOL = "refcheck"

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


class NCBIError(RuntimeError):
    pass


class PubMedClient:
    """Thin, rate limited E-utilities client. Swap `get` in tests."""

    def __init__(self, email, api_key=None, log=None, timeout=30):
        if not email or "@" not in email:
            raise ValueError("NCBI asks every tool to send a contact email. Please provide one.")
        self.email, self.api_key = email.strip(), (api_key or "").strip() or None
        self.delay = 0.11 if self.api_key else 0.35   # 10 vs 3 requests per second
        self.log = log or (lambda msg: None)
        self.timeout = timeout
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"{TOOL}/1.0 ({self.email})"
        self.cache = {}

    def get(self, endpoint, params, retries=4):
        params = dict(params, tool=TOOL, email=self.email)
        if self.api_key:
            params["api_key"] = self.api_key
        key = (endpoint, tuple(sorted(params.items())))
        if key in self.cache:
            return self.cache[key]
        for attempt in range(retries):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                method = self.session.post if endpoint == "efetch.fcgi" else self.session.get
                kw = {"data": params} if method == self.session.post else {"params": params}
                r = method(NCBI + endpoint, timeout=self.timeout, **kw)
                self._last = time.time()
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(1.5 * 2 ** attempt)
                    continue
                r.raise_for_status()
                self.cache[key] = r.text
                return r.text
            except requests.RequestException as e:
                self._last = time.time()
                if attempt == retries - 1:
                    raise NCBIError(f"{endpoint}: {e}") from e
                time.sleep(1.5 * 2 ** attempt)
        raise NCBIError(f"{endpoint}: repeated rate limit / server errors")

    # ---------------------------------------------------------------- calls --

    def esearch(self, term, retmax=10):
        root = ET.fromstring(self.get("esearch.fcgi", {
            "db": "pubmed", "term": term, "retmax": retmax, "retmode": "xml", "sort": "relevance"}))
        ids = [x.text for x in root.findall("./IdList/Id") if x.text]
        self.log(f"esearch hits={root.findtext('Count', '?')}  {term}")
        for tag in ("PhraseNotFound", "QuotedPhraseNotFound", "FieldNotFound"):
            for x in root.iter(tag):
                if x.text:
                    self.log(f"    {tag}: {x.text}")
        return ids

    def ecitmatch(self, journal, year, volume, page, author):
        if not (journal and year and page):
            return []
        bdata = "|".join([journal, year, volume or "", page, author or "", "k", ""])
        text = self.get("ecitmatch.cgi", {"db": "pubmed", "retmode": "xml", "bdata": bdata})
        out = [p.strip() for ln in text.splitlines() for p in [ln.split("|")[-1]] if p.strip().isdigit()]
        self.log(f"ecitmatch {bdata} -> {out or 'none'}")
        return out

    def efetch(self, pmids):
        pmids = [p for p in dict.fromkeys(pmids) if p]
        out = []
        for i in range(0, len(pmids), 150):
            xml = self.get("efetch.fcgi", {"db": "pubmed", "id": ",".join(pmids[i:i + 150]), "retmode": "xml"})
            out.extend(parse_pubmed_xml(xml))
        return out


# ------------------------------------------------------------- XML parse ----

def _txt(el, path, default=""):
    x = el.find(path)
    return "".join(x.itertext()).strip() if x is not None else default


def parse_pubmed_xml(xml_text):
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
        ids = {i.get("IdType", "").lower(): (i.text or "").strip()
               for i in art.findall("PubmedData/ArticleIdList/ArticleId")}
        doi = ids.get("doi", "") or next(
            (e.text for e in a.findall("ELocationID") if e.get("EIdType") == "doi" and e.text), "")
        abstract = "\n".join(
            ((t.get("Label") + ": ") if t.get("Label") else "") + "".join(t.itertext()).strip()
            for t in a.findall("Abstract/AbstractText"))
        pages = _txt(a, "Pagination/MedlinePgn") or next(
            (e.text for e in a.findall("ELocationID") if e.get("EIdType") == "pii" and e.text), "")
        recs.append(dict(
            pmid=_txt(mc, "PMID"), pmcid=ids.get("pmc", ""),
            title=_txt(a, "ArticleTitle").rstrip("."),
            journal=_txt(a, "Journal/Title"), iso_journal=_txt(a, "Journal/ISOAbbreviation"),
            year=year, volume=_txt(a, "Journal/JournalIssue/Volume"),
            issue=_txt(a, "Journal/JournalIssue/Issue"), pages=pages,
            authors="; ".join(authors), authors_full=full,
            first_author=(authors[0].rsplit(" ", 1)[0] if authors and " " in authors[0] else (authors[0] if authors else "")),
            doi=doi, abstract=abstract,
            keywords=[k.text for k in mc.findall("KeywordList/Keyword") if k.text],
            pubtypes=[p.text for p in a.findall("PublicationTypeList/PublicationType") if p.text],
            language=_txt(a, "Language"),
        ))
    return recs


# ---------------------------------------------------------------- scoring ---

def _norm_doi(d):
    d = (d or "").lower().strip()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d)
    return d.rstrip(".")


def score_candidate(ref, cand):
    reasons, score = [], 0
    ts = similarity(ref["title"], cand["title"])
    to = token_overlap(ref["title"], cand["title"])
    doi_hit = bool(ref.get("doi") and cand.get("doi") and _norm_doi(ref["doi"]) == _norm_doi(cand["doi"]))
    pmid_hit = bool(ref.get("pmid_hint") and ref["pmid_hint"] == cand["pmid"])

    if ts >= 0.98:
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

    ra, ca = first_author_surname(ref["authors"]), clean_text(cand["first_author"])
    if ra and ca:
        if ra == ca or ra.replace(" ", "") == ca.replace(" ", ""):
            score += 15; reasons.append("first-author")
        elif len(ra) >= 5 and ra[:5] == ca[:5]:
            score += 8; reasons.append("author-partial")

    if ref["year"] and cand["year"]:
        dy = abs(int(ref["year"]) - int(cand["year"]))
        if dy == 0:
            score += 10; reasons.append("year")
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


def classify(best, second):
    if best is None:
        return "NO_MATCH"
    gap = best["score"] - (second["score"] if second else 0)
    if best["score"] >= AUTO_SCORE and gap >= 5:
        return "AUTO_MATCH"
    if best["score"] >= REVIEW_SCORE:
        return "REVIEW"
    return "NO_MATCH"


# -------------------------------------------------------------- searching ---

def title_terms(title, n=14):
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


def match_reference(client, ref, max_candidates=40):
    """Return a result dict with status, best and runner up candidates."""
    seen, found, methods = set(), {}, {}

    def rank():
        scored = []
        for c in found.values():
            s, ts, to, why = score_candidate(ref, c)
            if s > 0:
                scored.append(dict(c, score=s, title_similarity=round(ts, 3),
                                   token_overlap=round(to, 3), match_reasons=";".join(why),
                                   search_methods=";".join(methods.get(c["pmid"], []))))
        scored.sort(key=lambda x: (x["score"], x["title_similarity"]), reverse=True)
        return scored

    if not (ref.get("title") or ref.get("doi") or ref.get("pmid_hint")):
        return dict(status="NO_MATCH", best=None, second=None, note="no title parsed")

    errors = []
    for method, kind, payload in _plan(ref):
        try:
            if kind == "ids":
                pmids = payload
            elif kind == "cit":
                pmids = client.ecitmatch(*payload)
            else:
                pmids = client.esearch(payload, retmax=10)
        except Exception as e:  # keep going with the other strategies
            errors.append(f"{method}: {e}")
            continue
        new = [p for p in pmids if p not in seen][: max(0, max_candidates - len(seen))]
        for p in pmids:
            methods.setdefault(p, []).append(method)
        if new:
            seen.update(new)
            try:
                for c in client.efetch(new):
                    found[c["pmid"]] = c
            except Exception as e:
                errors.append(f"efetch: {e}")
        scored = rank()
        if scored and classify(scored[0], scored[1] if len(scored) > 1 else None) == "AUTO_MATCH":
            break   # confident; skip the remaining searches

    scored = rank()
    best = scored[0] if scored else None
    second = scored[1] if len(scored) > 1 else None
    status = classify(best, second)
    if status == "NO_MATCH" and errors and not found:
        status = "ERROR"
    return dict(status=status, best=best, second=second, note="; ".join(errors))
