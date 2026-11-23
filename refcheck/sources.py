"""Secondary evidence sources (all optional, all audited and cached).

| Source                 | Used for                                                     | Sent                     |
|------------------------|--------------------------------------------------------------|--------------------------|
| Crossref REST API      | DOI metadata, retractions and corrections (publisher and     | DOI; or title, first     |
|                        | Retraction Watch), preprint links, existence outside PubMed  | author, journal, year    |
| OpenAlex               | existence outside PubMed, is_retracted, open access URL      | DOI; or title words      |
| doi.org handle API     | whether a DOI is registered at all                           | DOI                      |
| NLM Catalog (NCBI)     | journal known to NLM, currently indexed in MEDLINE           | journal abbreviation,    |
|                        |                                                              | ISSN                     |
| DOAJ                   | journal listed in the Directory of Open Access Journals      | ISSN                     |
| Unpaywall              | legal open access copy                                       | DOI                      |
| Retraction Watch CSV   | retractions, corrections, concerns, fully offline            | nothing (local file)     |

Crossref and Unpaywall receive the contact email (their polite access rules ask for it).
Field names follow the services' documentation as of November 2026; parsers are defensive
because services evolve. The Crossref retraction rules are explained in `crossref_updates`.
"""

from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests

from .netaudit import DiskCache, NetworkAudit
from .textutil import clean_text, similarity

ALL_SOURCES = ("crossref", "openalex", "doi", "nlmcatalog", "doaj", "unpaywall", "retractionwatch")

HOSTS = {
    "crossref": "https://api.crossref.org",
    "openalex": "https://api.openalex.org",
    "doi": "https://doi.org",
    "doaj": "https://doaj.org",
    "unpaywall": "https://api.unpaywall.org",
}
DELAY = {"crossref": 0.12, "openalex": 0.12, "doi": 0.1, "doaj": 0.25, "unpaywall": 0.12}

RETRACT_TYPES = {"retraction", "withdrawal", "removal", "partial_retraction"}
CONCERN_TYPES = {"expression_of_concern", "expression-of-concern"}
CORRECT_TYPES = {"correction", "erratum", "corrigendum", "addendum", "clarification", "new_version"}
NOTICE_TITLE = re.compile(r"^\s*(retraction|retracted|notice of retraction|withdrawn|correction|corrigendum|"
                          r"erratum|expression of concern)\b", re.I)


class SourceError(RuntimeError):
    kind = "error"


# ------------------------------------------------------------------ transport --

class JSONService:
    """Rate limited, cached, audited GET of JSON. Tests replace `_fetch`."""

    def __init__(self, email: str = "", audit: Optional[NetworkAudit] = None,
                 cache: Optional[DiskCache] = None, timeout: float = 20, offline: bool = False):
        self.email = (email or "").strip()
        self.audit = audit or NetworkAudit()
        self.cache = cache
        self.timeout = timeout
        self.offline = offline
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"refcheck/2.0 (mailto:{self.email})" if self.email else "refcheck/2.0"
        self._last: Dict[str, float] = {}
        self.memo: Dict[Tuple, Any] = {}
        self.down: Dict[str, str] = {}     # service -> reason; not retried again this session

    def _fetch(self, url: str, params: Dict[str, Any]) -> Tuple[int, str]:
        r = self.session.get(url, params=params, timeout=self.timeout)
        return r.status_code, r.text

    def get_json(self, service: str, path: str, params: Optional[Dict[str, Any]] = None,
                 retries: int = 3) -> Optional[Any]:
        """Parsed JSON, or None for 'not found' (HTTP 404). Raises SourceError otherwise."""
        params = dict(params or {})
        host = HOSTS[service].split("//", 1)[1]
        endpoint = f"{service}:{path}"
        key = (endpoint, tuple(sorted((k, str(v)) for k, v in params.items() if k not in ("mailto", "email"))))
        if key in self.memo:
            return self.memo[key]
        if self.cache is not None:
            hit = self.cache.get(endpoint, params, ignore_ttl=self.offline)
            if hit is not None:
                self.audit.add(endpoint, params, True, "cached", len(hit), host=host)
                val = None if hit == "__404__" else json.loads(hit)
                self.memo[key] = val
                return val
        if self.offline:
            self.audit.add(endpoint, params, True, "offline_cache_miss", host=host)
            raise SourceError(f"{service}: offline and not cached")
        if service in self.down:
            raise SourceError(f"{service}: {self.down[service]} (skipped)")
        err = None
        for attempt in range(retries):
            wait = DELAY.get(service, 0.2) - (time.time() - self._last.get(service, 0))
            if wait > 0:
                time.sleep(wait)
            t0 = time.time()
            try:
                status, text = self._fetch(HOSTS[service] + path, params)
            except requests.RequestException as e:
                err = f"{type(e).__name__}"
                self._last[service] = time.time()
                self.audit.add(endpoint, params, False, "connection", 0, time.time() - t0, host)
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                self.down[service] = "not reachable"     # stop asking an unreachable host
                break
            self._last[service] = time.time()
            if status == 404:
                self.audit.add(endpoint, params, False, "ok", len(text), time.time() - t0, host)
                self._store(endpoint, params, key, "__404__")
                return None
            if status == 429 or status >= 500:
                err = f"HTTP {status}"
                self.audit.add(endpoint, params, False, "rate_limited" if status == 429 else "server_error",
                               0, time.time() - t0, host)
                time.sleep(1.5 * 2 ** attempt)
                continue
            if status >= 400:
                self.audit.add(endpoint, params, False, "bad_response", len(text), time.time() - t0, host)
                raise SourceError(f"{service}: HTTP {status}")
            try:
                val = json.loads(text)
            except ValueError:
                self.audit.add(endpoint, params, False, "bad_response", len(text), time.time() - t0, host)
                raise SourceError(f"{service}: answer is not JSON")
            self.audit.add(endpoint, params, False, "ok", len(text), time.time() - t0, host)
            self._store(endpoint, params, key, text)
            self.memo[key] = val
            return val
        if err and err.startswith("HTTP"):
            self.down[service] = f"repeated {err}"
        raise SourceError(f"{service}: {err or 'failed'}")

    def _store(self, endpoint, params, key, text):
        if text == "__404__":
            self.memo[key] = None
        if self.cache is not None:
            try:
                self.cache.put(endpoint, params, text)
            except Exception as e:      # a cache problem must not stop the check
                self.cache_errors = getattr(self, "cache_errors", []) + [type(e).__name__]


# -------------------------------------------------------------- parsers ------

def _first(x, default=""):
    if isinstance(x, list):
        return x[0] if x else default
    return x if x is not None else default


def _norm_doi(d: str) -> str:
    d = (d or "").strip().lower()
    d = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", d)
    return re.sub(r"^doi:\s*", "", d).rstrip(".,;)]")


def crossref_updates(work: Dict[str, Any]) -> List[Dict[str, str]]:
    """Notices that concern THIS work, as {'kind', 'type', 'doi', 'source', 'label'}.

    Crossref documents retractions in `update-to`, with `source` = 'publisher' or
    'retraction-watch' (crossref.org, Retraction Watch documentation, 2026). Rules:
      * `updated-by` entries (if present) always concern this work.
      * `update-to` entries from Retraction Watch annotate the retracted work itself.
      * a publisher `update-to` entry on a record whose title looks like a notice
        ("Retraction: ...", "Correction to ...") means this record IS the notice; it does
        not concern this work and is reported as kind 'is_notice'.
      * otherwise a publisher `update-to` entry is taken to concern this work.
    """
    title = _first(work.get("title"))
    looks_notice = bool(NOTICE_TITLE.match(title or ""))
    out = []
    for field in ("updated-by", "update-to"):
        for u in work.get(field) or []:
            t = (u.get("type") or "").lower().replace(" ", "_")
            src = (u.get("source") or "publisher").lower()
            if field == "update-to" and src != "retraction-watch" and looks_notice:
                kind = "is_notice"
            elif t in RETRACT_TYPES:
                kind = "retracted"
            elif t in CONCERN_TYPES:
                kind = "concern"
            elif t in CORRECT_TYPES:
                kind = "corrected"
            else:
                continue
            out.append(dict(kind=kind, type=t, doi=(u.get("DOI") or "").lower(), source=src,
                            label=u.get("label") or t, record_id=str(u.get("record-id") or "")))
    return out


def parse_crossref_work(w: Dict[str, Any]) -> Dict[str, Any]:
    issued = (w.get("issued") or w.get("published") or {}).get("date-parts") or [[None]]
    authors = w.get("author") or []
    rel = w.get("relation") or {}
    return dict(
        source="Crossref", doi=(w.get("DOI") or "").lower(), title=_first(w.get("title")),
        journal=_first(w.get("container-title")), short_journal=_first(w.get("short-container-title")),
        issn=list(w.get("ISSN") or []), year=str(issued[0][0] or ""), volume=w.get("volume") or "",
        issue=w.get("issue") or "", pages=w.get("page") or "", type=w.get("type") or "",
        first_author=(authors[0].get("family") or authors[0].get("name") or "") if authors else "",
        updates=crossref_updates(w),
        related=sorted({r.get("id", "").lower() for v in rel.values() if isinstance(v, list) for r in v
                        if isinstance(r, dict) and r.get("id-type") == "doi" and r.get("id")}),
        preprint_of=[r.get("id", "").lower() for r in rel.get("is-preprint-of", []) if r.get("id-type") == "doi"],
        has_preprint=[r.get("id", "").lower() for r in rel.get("has-preprint", []) if r.get("id-type") == "doi"],
    )


def parse_openalex_work(w: Dict[str, Any]) -> Dict[str, Any]:
    ids = w.get("ids") or {}
    pm = str(ids.get("pmid") or "")
    src = ((w.get("primary_location") or {}).get("source") or {})
    auth = w.get("authorships") or []
    oa = w.get("open_access") or {}
    bib = w.get("biblio") or {}
    return dict(
        source="OpenAlex", doi=_norm_doi(w.get("doi") or ids.get("doi") or ""),
        pmid=re.sub(r"\D", "", pm.rsplit("/", 1)[-1]) if pm else "",
        title=w.get("title") or w.get("display_name") or "", year=str(w.get("publication_year") or ""),
        journal=src.get("display_name") or "", issn=list(src.get("issn") or []),
        volume=bib.get("volume") or "", issue=bib.get("issue") or "",
        pages="-".join(x for x in (bib.get("first_page"), bib.get("last_page")) if x),
        type=w.get("type") or "", is_retracted=bool(w.get("is_retracted")),
        first_author=((auth[0].get("author") or {}).get("display_name") or "") if auth else "",
        oa_url=oa.get("oa_url") or "",
    )


# ------------------------------------------------------------- ISSN ---------

ISSN_RE = re.compile(r"\b(\d{4})-?(\d{3}[\dXx])\b")


def issn_valid(issn: str) -> bool:
    """ISO 3297 check digit."""
    m = re.fullmatch(r"(\d{4})-?(\d{3})([\dXx])", (issn or "").strip())
    if not m:
        return False
    digits = m.group(1) + m.group(2)
    total = sum(int(d) * w for d, w in zip(digits, range(8, 1, -1)))
    check = (11 - total % 11) % 11
    return m.group(3).upper() == ("X" if check == 10 else str(check))


def issns_in_text(text: str) -> List[str]:
    return [f"{a}-{b.upper()}" for a, b in re.findall(r"ISSN\s*:?\s*(\d{4})-?(\d{3}[\dXx])", text or "", re.I)]


# ----------------------------------------------------- Retraction Watch -----

def _name_keys(authors: str) -> set:
    """Compact forms under which an author list can be cited: each word of each name
    (covers given and family names in either order) and each run of final words
    ("Newsome-Cuby", "Al-Hamadani", "de Melo e Silva")."""
    keys = set()
    for a in authors.split(";"):
        w = clean_text(a).split()
        keys.update(x for x in w if len(x) >= 2)
        keys.update("".join(w[i:]) for i in range(len(w)) if len("".join(w[i:])) >= 2)
    return keys


class RetractionWatchIndex:
    """Local copy of the Retraction Watch dataset published by Crossref
    (gitlab.com/crossref/retraction-watch-data, updated each working day; see rwdata.py).
    Lookups are by original paper DOI or PubMed ID, or, for a reference with neither
    identifier found, by exact normalised title plus an author surname. All offline."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.by_doi: Dict[str, List[Dict[str, str]]] = {}
        self.by_pmid: Dict[str, List[Dict[str, str]]] = {}
        self.by_title: Dict[str, List[Dict[str, str]]] = {}
        with open(self.path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            for row in csv.DictReader(fh):
                reason = (row.get("Reason") or "").strip()
                rec = dict(nature=(row.get("RetractionNature") or "").strip(),
                           date=(row.get("RetractionDate") or "").strip(),
                           notice_doi=(row.get("RetractionDOI") or "").strip().lower(),
                           notice_pmid=(row.get("RetractionPubMedID") or "").strip(),
                           reason=reason, record_id=(row.get("Record ID") or "").strip(),
                           upgraded=("updated to retraction" in reason.lower()))
                d = _norm_doi(row.get("OriginalPaperDOI") or "")
                p = re.sub(r"\D", "", row.get("OriginalPaperPubMedID") or "")
                if d and d not in ("unavailable", "na"):
                    self.by_doi.setdefault(d, []).append(rec)
                if p and p != "0":
                    self.by_pmid.setdefault(p, []).append(rec)
                t = clean_text(row.get("Title") or "")
                if len(t) >= 30:
                    surnames = _name_keys(row.get("Author") or "")
                    year = (re.findall(r"(\d{4})", row.get("OriginalPaperDate") or "") or [""])[0]
                    self.by_title.setdefault(t, []).append(dict(rec, _surnames=surnames, _year=year))

    def lookup(self, doi: str = "", pmid: str = "") -> List[Dict[str, str]]:
        out = list(self.by_doi.get(_norm_doi(doi), [])) if doi else []
        for r in (self.by_pmid.get(pmid, []) if pmid else []):
            if r not in out:
                out.append(r)
        return out

    def lookup_title(self, title: str, first_author: str = "", year: str = "") -> List[Dict[str, str]]:
        """Rows with the same normalised title (at least 30 characters) whose authors include
        the reference's first author surname, and whose year, when both are known, is within
        one year. Used only when the DOI and PMID found nothing."""
        t = clean_text(title or "")
        fa = re.sub(r"[^a-z0-9]", "", clean_text(first_author or ""))
        if len(t) < 30 or len(fa) < 2:
            return []
        out = []
        for r in self.by_title.get(t, []):
            if fa not in r["_surnames"]:
                continue
            if year and r["_year"] and year.isdigit() and abs(int(year) - int(r["_year"])) > 1:
                continue
            out.append({k: v for k, v in r.items() if not k.startswith("_")} | {"matched_by": "title and author"})
        return out

    @staticmethod
    def kind(nature: str) -> str:
        n = nature.lower()
        if "concern" in n:
            return "concern"
        if "correction" in n:
            return "corrected"
        if "reinstat" in n:
            return "reinstated"
        return "retracted"


# ----------------------------------------------------------- the gatherer ---

class Evidence:
    """Runs the enabled secondary sources for references and journals."""

    def __init__(self, service: JSONService, pubmed_client=None, enabled: Iterable[str] = ALL_SOURCES,
                 rw_index: Optional[RetractionWatchIndex] = None):
        self.s = service
        self.pm = pubmed_client
        self.enabled = set(enabled)
        self.rw = rw_index
        self.rw_status = None          # rwdata.UpdateResult when the copy was updated this run
        self.errors: List[str] = []

    def _try(self, fn, *a):
        try:
            return fn(*a)
        except SourceError as e:
            self.errors.append(str(e))
            return None
        except Exception as e:  # a parser surprise must never stop the run
            self.errors.append(f"{getattr(fn, '__name__', 'source')}: {type(e).__name__}")
            return None

    # -- single lookups ------------------------------------------------------
    def crossref_doi(self, doi):
        j = self.s.get_json("crossref", "/works/" + quote(_norm_doi(doi), safe="/"), {"mailto": self.s.email})
        return parse_crossref_work(j["message"]) if j and "message" in j else None

    def crossref_search(self, ref):
        q = " ".join(x for x in (ref.get("title", ""), (ref.get("authors") or "").split(",")[0],
                                  ref.get("journal", ""), ref.get("year", "")) if x)
        j = self.s.get_json("crossref", "/works", {"query.bibliographic": q[:300], "rows": 5,
                                                    "mailto": self.s.email})
        items = ((j or {}).get("message") or {}).get("items") or []
        return [parse_crossref_work(w) for w in items]

    def openalex_doi(self, doi):
        j = self.s.get_json("openalex", "/works/doi:" + quote(_norm_doi(doi), safe="/"), {"mailto": self.s.email})
        return parse_openalex_work(j) if j else None

    def openalex_search(self, ref):
        title = re.sub(r"[,:|]", " ", ref.get("title", ""))[:250]
        j = self.s.get_json("openalex", "/works", {"filter": f"title.search:{title}", "per_page": 5,
                                                    "mailto": self.s.email})
        return [parse_openalex_work(w) for w in (j or {}).get("results") or []]

    def doi_registered(self, doi):
        j = self.s.get_json("doi", "/api/handles/" + quote(_norm_doi(doi), safe="/"))
        if j is None:
            return False
        code = j.get("responseCode")
        return True if code == 1 else (False if code == 100 else None)

    def doaj_listed(self, issn):
        j = self.s.get_json("doaj", "/api/search/journals/" + quote(f"issn:{issn}"))
        if j is None:
            return False
        return bool(j.get("total") or j.get("results"))

    def unpaywall(self, doi):
        if not self.s.email:
            return None
        j = self.s.get_json("unpaywall", "/v2/" + quote(_norm_doi(doi), safe="/"), {"email": self.s.email})
        if not j or not j.get("is_oa"):
            return ""
        loc = j.get("best_oa_location") or {}
        return loc.get("url") or loc.get("url_for_pdf") or ""

    def nlm_catalog(self, journal, issns):
        """{'found': bool, 'indexed': bool} from the NLM Catalog ([ta], [is], currentlyindexed)."""
        terms = [f'"{i}"[is]' for i in issns if issn_valid(i)] or ([f'"{journal}"[ta]'] if journal else [])
        if not terms or self.pm is None:
            return None
        base = " OR ".join(terms)
        found = self.pm.esearch(f"({base})", retmax=1, db="nlmcatalog")
        if not found:
            return dict(found=False, indexed=False)
        indexed = self.pm.esearch(f"({base}) AND currentlyindexed", retmax=1, db="nlmcatalog")
        return dict(found=True, indexed=bool(indexed))

    # -- per reference -------------------------------------------------------
    def for_reference(self, ref, pubmed_rec=None):
        """Evidence dict for one reference. pubmed_rec: the PubMed record finally chosen."""
        ev: Dict[str, Any] = {}
        doi = _norm_doi(ref.get("doi") or "")
        rec_doi = _norm_doi((pubmed_rec or {}).get("doi") or "")
        pmid = (pubmed_rec or {}).get("pmid", "")
        look = rec_doi or doi
        if doi and "crossref" in self.enabled:
            ev["crossref_written_doi"] = self._try(self.crossref_doi, doi)
        if rec_doi and rec_doi != doi and "crossref" in self.enabled:
            ev["crossref_record"] = self._try(self.crossref_doi, rec_doi)
        elif doi and doi == rec_doi:
            ev["crossref_record"] = ev.get("crossref_written_doi")
        if doi and "doi" in self.enabled and not ev.get("crossref_written_doi"):
            ev["doi_registered"] = self._try(self.doi_registered, doi)
        if look and "openalex" in self.enabled:
            ev["openalex_record"] = self._try(self.openalex_doi, look)
        if look and "unpaywall" in self.enabled:
            ev["oa_url"] = self._try(self.unpaywall, look)
        if self.rw is not None:
            ev["retraction_watch"] = self.rw.lookup(look, pmid)
            if not ev["retraction_watch"]:
                from .textutil import first_author_surname
                ev["retraction_watch"] = self.rw.lookup_title(ref.get("title", ""),
                                                              first_author_surname(ref.get("authors", "")),
                                                              ref.get("year", ""))
        if not pubmed_rec:
            hits, answered = [], []          # only sources that actually answered count as searched
            for name, fn in (("crossref", self.crossref_search), ("openalex", self.openalex_search)):
                if name in self.enabled and ref.get("title"):
                    got = self._try(fn, ref)
                    if got is not None:
                        hits += got
                        answered.append(name)
            ev["searched_elsewhere"] = answered
            ev["elsewhere"] = best_external(ref, hits)
            if ev["elsewhere"] and self.rw is not None and ev["elsewhere"].get("doi"):
                ev["retraction_watch"] = self.rw.lookup(ev["elsewhere"]["doi"], ev["elsewhere"].get("pmid", ""))
        if ev.get("oa_url") in (None, "") and (ev.get("openalex_record") or {}).get("oa_url"):
            ev["oa_url"] = ev["openalex_record"]["oa_url"]
        return ev

    def for_journal(self, journal, issns):
        ev: Dict[str, Any] = {"issns": issns, "invalid_issns": [i for i in issns if not issn_valid(i)]}
        if "nlmcatalog" in self.enabled:
            ev["nlm"] = self._try(self.nlm_catalog, journal, issns)
        if "doaj" in self.enabled:
            valid = [i for i in issns if issn_valid(i)]
            ev["doaj"] = any(self._try(self.doaj_listed, i) for i in valid) if valid else None
        return ev


def _year(v):
    m = re.search(r"(1[89]|20)\d\d", str(v or ""))
    return int(m.group(0)) if m else None


def best_external(ref, hits, min_title=0.90):
    """The external record that is clearly the same work as the reference, or None."""
    best, best_s = None, 0.0
    for h in hits:
        s = similarity(ref.get("title", ""), h.get("title", ""))
        ry, hy = _year(ref.get("year")), _year(h.get("year"))
        y_ok = ry is None or hy is None or abs(ry - hy) <= 1
        if s >= min_title and y_ok and s > best_s:
            best, best_s = dict(h, title_similarity=round(s, 3)), s
    return best


def gather(refs, decisions, records, evidence: Evidence):
    """{ref_num: evidence} and {journal key: journal evidence}."""
    per_ref, journals = {}, {}
    for ref in refs:
        pm = decisions.get(ref["num"], ("", ""))[0]
        rec = records.get(pm) if pm else None
        ev = evidence.for_reference(ref, rec)
        jname = (rec or {}).get("iso_journal") or ref.get("journal", "")
        issns = list(dict.fromkeys((rec or {}).get("issn", []) + issns_in_text(ref.get("text", ""))
                                   + (((ev.get("elsewhere") or {}).get("issn")) or [])))
        key = clean_text(jname) + "|" + ",".join(issns)
        if (jname or issns) and key not in journals:
            journals[key] = evidence.for_journal(jname, issns)
        ev["journal_key"] = key if (jname or issns) else ""
        per_ref[ref["num"]] = ev
    return per_ref, journals


def evidence_from_settings(settings, pubmed_client=None, email: str = "", enabled=None, rw_session=None):
    """Evidence object sharing the PubMed client's audit, cache and offline mode.

    Retraction Watch: a file named in ``retraction_watch_csv`` is used as is. Otherwise, with
    the ``retractionwatch`` source enabled, the copy in ``retraction_watch_dir`` is brought up
    to date first (rwdata.ensure_current: a checksum request, and a download only when
    GitLab holds a newer version). The outcome is kept on ``Evidence.rw_status``.

    Returns None when no secondary source is enabled and no Retraction Watch file is usable."""
    from .rwdata import ensure_current
    enabled = [x for x in (settings.sources if enabled is None else enabled) if x in ALL_SOURCES]
    offline = getattr(pubmed_client, "offline", settings.offline)
    audit = getattr(pubmed_client, "audit", None)
    rw, rw_status = None, None
    if getattr(settings, "retraction_watch_csv", ""):
        rw = RetractionWatchIndex(settings.retraction_watch_csv)
    elif "retractionwatch" in enabled:
        rw_status = ensure_current(getattr(settings, "retraction_watch_dir", ""), offline=offline,
                                   session=rw_session, audit=audit)
        if rw_status.usable:
            rw = RetractionWatchIndex(rw_status.path)
    web = [x for x in enabled if x != "retractionwatch"]
    if not web and rw is None:
        return None
    svc = JSONService(email or getattr(pubmed_client, "email", "") or settings.ncbi_email,
                      audit=audit, cache=getattr(pubmed_client, "cache", None), offline=offline)
    ev = Evidence(svc, pubmed_client, web, rw)
    ev.rw_status = rw_status
    return ev
