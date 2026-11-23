"""Offline PubMed fixtures (real records for PMIDs 41092086, 35357562, 40392954,
42152226, 40481322, as returned by PubMed) and a mock E-utilities client."""
import re
from xml.sax.saxutils import escape

from refcheck.config import Settings
from refcheck.pubmed import PubMedClient

# The exact opening lines NCBI E-utilities send (checked against live answers): the mock must
# send them too, or a parser that rejects real answers would pass every test.
ESEARCH_HEAD = ('<?xml version="1.0" encoding="UTF-8" ?>\n<!DOCTYPE eSearchResult PUBLIC "-//NLM//DTD esearch '
                '20060628//EN" "https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20060628/esearch.dtd">\n')
EFETCH_HEAD = ('<?xml version="1.0" ?>\n<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January '
               '2025//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">\n')

ARTICLES = [
    dict(pmid="41092086", title="CBTRUS Statistical Report: Primary Brain and Other Central Nervous System Tumors Diagnosed in the United States in 2018-2022.",
         journal="Neuro-oncology", iso="Neuro Oncol", year="2025", vol="27", iss="Supplement_4", pg="iv1-iv66",
         doi="10.1093/neuonc/noaf194", pmc="PMC12527013",
         authors=[('Price', 'Mackenzie', 'M'), ('Ballard', 'Christine Ann Pittman', 'CAP'), ('Benedetti', 'Julia R', 'JR'), ('Kruchko', 'Carol', 'C'), ('Barnholtz-Sloan', 'Jill S', 'JS'), ('Ostrom', 'Quinn T', 'QT')]),
    dict(pmid="35357562", title="Rapid-CNS<sup>2</sup>: rapid comprehensive adaptive nanopore-sequencing of CNS tumors, a proof-of-concept study.",
         journal="Acta neuropathologica", iso="Acta Neuropathol", year="2022", vol="143", iss="5", pg="609-612",
         doi="10.1007/s00401-022-02415-6", pmc="PMC9038836",
         authors=[('Patel', 'Areeba', 'A'), ('Dogan', 'Helin', 'H'), ('Payne', 'Alexander', 'A'), ('Krause', 'Elena', 'E'), ('Sievers', 'Philipp', 'P'), ('Schoebe', 'Natalie', 'N'), ('Schrimpf', 'Daniel', 'D'), ('Blume', 'Christina', 'C'), ('Stichel', 'Damian', 'D'), ('Holmes', 'Nadine', 'N'), ('Euskirchen', 'Philipp', 'P'), ('Hench', 'Jürgen', 'J'), ('Frank', 'Stephan', 'S'), ('Rosenstiel-Goidts', 'Violaine', 'V'), ('Ratliff', 'Miriam', 'M'), ('Etminan', 'Nima', 'N'), ('Unterberg', 'Andreas', 'A'), ('Dieterich', 'Christoph', 'C'), ('Herold-Mende', 'Christel', 'C'), ('Pfister', 'Stefan M', 'SM'), ('Wick', 'Wolfgang', 'W'), ('Loose', 'Matthew', 'M'), ('von Deimling', 'Andreas', 'A'), ('Sill', 'Martin', 'M'), ('Jones', 'David T W', 'DTW'), ('Schlesner', 'Matthias', 'M'), ('Sahm', 'Felix', 'F')]),
    dict(pmid="40392954", title="ROBIN: A unified nanopore-based assay integrating intraoperative methylome classification and next-day comprehensive profiling for ultra-rapid tumor diagnosis.",
         journal="Neuro-oncology", iso="Neuro Oncol", year="2025", vol="27", iss="8", pg="2035-2046",
         doi="10.1093/neuonc/noaf103", pmc="PMC12448888",
         authors=[('Deacon', 'Simon', 'S'), ('Cahyani', 'Inswasti', 'I'), ('Holmes', 'Nadine', 'N'), ('Fox', 'Graeme', 'G'), ('Munro', 'Rory', 'R'), ('Wibowo', 'Satrio', 'S'), ('Murray', 'Thomas', 'T'), ('Mason', 'Hannah', 'H'), ('Housley', 'Mark', 'M'), ('Martin', 'Daniel', 'D'), ('Sharif', 'Abdi', 'A'), ('Patel', 'Areeba', 'A'), ('Goldspring', 'Robert', 'R'), ('Brandner', 'Sebastian', 'S'), ('Sahm', 'Felix', 'F'), ('Smith', 'Stuart', 'S'), ('Paine', 'Simon', 'S'), ('Loose', 'Matthew', 'M')]),
    dict(pmid="42152226", title="cIMPACT-NOW Update 12: Refining pathology-based risk stratification and grading for IDH-mutant gliomas.",
         journal="Neuro-oncology", iso="Neuro Oncol", year="2026", vol="28", iss="9", pg="2069-2089",
         doi="10.1093/neuonc/noag108", pmc="PMC13550680",
         authors=[('Brat', 'Daniel J', 'DJ'), ('Aldape', 'Kenneth', 'K'), ('French', 'Pim J', 'PJ'), ('Louis', 'David N', 'DN'), ('Nasrallah', 'MacLean P', 'MP'), ('Reifenberger', 'Guido', 'G'), ('Reuss', 'David E', 'DE'), ('Touat', 'Mehdi', 'M'), ('van den Bent', 'Martin J', 'MJ'), ('Wesseling', 'Pieter', 'P'), ('Figarella-Branger', 'Dominique', 'D')]),
    dict(pmid="40481322", title="crossNN is an explainable framework for cross-platform DNA methylation-based classification of tumors.",
         journal="Nature cancer", iso="Nat Cancer", year="2025", vol="6", iss="7", pg="1283-94",
         doi="10.1038/s43018-025-00976-5", pmc="PMC12296554",
         authors=[('Yuan', 'Dongsheng', 'D'), ('Jugas', 'Robin', 'R'), ('Pokorna', 'Petra', 'P'), ('Sterba', 'Jaroslav', 'J'), ('Slaby', 'Ondrej', 'O'), ('Schmid', 'Simone', 'S'), ('Siewert', 'Christin', 'C'), ('Osberg', 'Brendan', 'B'), ('Capper', 'David', 'D'), ('Halldorsson', 'Skarphedinn', 'S'), ('Vik-Mo', 'Einar O', 'EO'), ('Zeiner', 'Pia S', 'PS'), ('Weber', 'Katharina J', 'KJ'), ('Harter', 'Patrick N', 'PN'), ('Thomas', 'Christian', 'C'), ('Albers', 'Anne', 'A'), ('Rechsteiner', 'Markus', 'M'), ('Reimann', 'Regina', 'R'), ('Appelt', 'Anton', 'A'), ('Schüller', 'Ulrich', 'U'), ('Jabareen', 'Nabil', 'N'), ('Mackowiak', 'Sebastian', 'S'), ('Ishaque', 'Naveed', 'N'), ('Eils', 'Roland', 'R'), ('Lukassen', 'Sören', 'S'), ('Euskirchen', 'Philipp', 'P')]),
]
BY_PMID = {a["pmid"]: a for a in ARTICLES}


def article_xml(a):
    pt = "".join(f"<PublicationType>{t}</PublicationType>" for t in a.get("pubtypes", ["Journal Article"]))
    cc = "".join(f'<CommentsCorrections RefType="{t}"><RefSource>{src}</RefSource><PMID>{pm}</PMID></CommentsCorrections>'
                 for t, src, pm in a.get("links", []))
    cc = f"<CommentsCorrectionsList>{cc}</CommentsCorrectionsList>" if cc else ""
    ad = f'<ArticleDate DateType="Electronic"><Year>{a["epub"]}</Year></ArticleDate>' if a.get("epub") else ""
    au = "".join(f"<Author><LastName>{escape(l)}</LastName><ForeName>{escape(f)}</ForeName><Initials>{i}</Initials></Author>"
                 for l, f, i in a["authors"])
    return f"""<PubmedArticle><MedlineCitation><PMID>{a['pmid']}</PMID><Article>
<Journal><JournalIssue><Volume>{a['vol']}</Volume><Issue>{a['iss']}</Issue><PubDate><Year>{a['year']}</Year></PubDate></JournalIssue>
{f"<ISSN>{a['issn']}</ISSN>" if a.get('issn') else ""}<Title>{a['journal']}</Title><ISOAbbreviation>{a['iso']}</ISOAbbreviation></Journal>
<ArticleTitle>{a['title']}</ArticleTitle><Pagination><MedlinePgn>{a['pg']}</MedlinePgn></Pagination>
<Abstract><AbstractText>Fixture abstract for {a['pmid']}.</AbstractText></Abstract>
<AuthorList>{au}</AuthorList><Language>eng</Language><PublicationTypeList>{pt}</PublicationTypeList>{ad}</Article>{cc}</MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="pubmed">{a['pmid']}</ArticleId><ArticleId IdType="doi">{a['doi']}</ArticleId>
<ArticleId IdType="pmc">{a['pmc']}</ArticleId></ArticleIdList></PubmedData></PubmedArticle>"""


def _plain(t):
    return re.sub(r"<[^>]+>", "", t).lower()


class MockClient(PubMedClient):
    """Replaces only the network transport; caching, audit and retries stay real.
    No disk cache is used unless one is passed in."""

    def __init__(self, articles=None, cache=None, offline=False, settings=None, fail=None):
        super().__init__("test@example.org", settings=settings or Settings(cache_path=""),
                         cache=cache, offline=offline)
        self.delay = 0
        self.calls = []
        self.fail = fail or {}                       # endpoint -> list of (status or exception)
        self.articles = ARTICLES if articles is None else articles
        self.by_pmid = {a["pmid"]: a for a in self.articles}

    def _fetch(self, endpoint, params):
        queue = self.fail.get(endpoint)
        if queue:
            f = queue.pop(0)
            if isinstance(f, Exception):
                raise f
            return f, "error"
        return 200, self._answer(endpoint, params)

    def _answer(self, endpoint, params):
        ARTICLES, BY_PMID = self.articles, self.by_pmid
        self.calls.append((endpoint, params.get("term") or params.get("bdata") or params.get("id")))
        if endpoint == "ecitmatch.cgi":
            j, y, v, p, au = params["bdata"].split("|")[:5]
            hit = [a for a in ARTICLES if a["iso"].lower() == j.lower() and a["year"] == y
                   and a["pg"].split("-")[0].lower() == p.lower()]
            return params["bdata"] + (hit[0]["pmid"] if hit else "NOT_FOUND")
        if endpoint == "esearch.fcgi" and params.get("db") == "nlmcatalog":
            # self.nlm: {lower case title abbreviation or ISSN: currently indexed (bool)}
            term = params["term"].lower()
            keys = re.findall(r'"([^"]+)"\[(?:ta|is)\]', term)
            hit = [k for k in keys if k in getattr(self, "nlm", {})]
            if "currentlyindexed" in term:
                hit = [k for k in hit if self.nlm[k]]
            return (ESEARCH_HEAD + "<eSearchResult><Count>%d</Count><IdList>%s</IdList></eSearchResult>"
                    % (len(hit), "".join(f"<Id>{100 + i}</Id>" for i, _ in enumerate(hit[:1]))))
        if endpoint == "esearch.fcgi":
            term = params["term"]
            ids = []
            for a in ARTICLES:
                m = re.search(r'"([^"]+)"\[aid\]', term)
                if m:
                    ok = m.group(1).lower() == a["doi"].lower()
                elif "[pg]" in term:
                    pg = re.search(r"(\S+)\[pg\]", term).group(1)
                    ok = a["pg"].split("-")[0].lower() == pg.lower()
                else:
                    words = re.findall(r"(\w+)\[Title\]", term)
                    phrase = re.search(r'"([^"]+)"\[Title\]', term)
                    t = _plain(a["title"])
                    ok = (all(re.search(r"\b" + w.lower() + r"\b", t) for w in words) if words else False) or \
                         (phrase is not None and phrase.group(1).lower().rstrip(".") in t)
                if ok:
                    ids.append(a["pmid"])
            return (ESEARCH_HEAD + "<eSearchResult><Count>%d</Count><IdList>%s</IdList></eSearchResult>"
                    % (len(ids), "".join(f"<Id>{i}</Id>" for i in ids)))
        if endpoint == "efetch.fcgi":
            body = "".join(article_xml(BY_PMID[i]) for i in params["id"].split(",") if i in BY_PMID)
            return f"{EFETCH_HEAD}<PubmedArticleSet>{body}</PubmedArticleSet>"
        raise AssertionError(endpoint)
