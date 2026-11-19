"""Offline PubMed fixtures (real records for PMIDs 41092086, 35357562, 40392954,
42152226, 40481322, as returned by PubMed) and a mock E-utilities client."""
import re
from xml.sax.saxutils import escape

from refcheck.pubmed import PubMedClient

ARTICLES = [
    dict(pmid="41092086", title="CBTRUS Statistical Report: Primary Brain and Other Central Nervous System Tumors Diagnosed in the United States in 2018-2022.",
         journal="Neuro-oncology", iso="Neuro Oncol", year="2025", vol="27", iss="Supplement_4", pg="iv1-iv66",
         doi="10.1093/neuonc/noaf194", pmc="PMC12527013",
         authors=[("Price", "Mackenzie", "M"), ("Ballard", "Christine Ann Pittman", "CAP"), ("Ostrom", "Quinn T", "QT")]),
    dict(pmid="35357562", title="Rapid-CNS<sup>2</sup>: rapid comprehensive adaptive nanopore-sequencing of CNS tumors, a proof-of-concept study.",
         journal="Acta neuropathologica", iso="Acta Neuropathol", year="2022", vol="143", iss="5", pg="609-612",
         doi="10.1007/s00401-022-02415-6", pmc="PMC9038836",
         authors=[("Patel", "Areeba", "A"), ("Dogan", "Helin", "H"), ("von Deimling", "Andreas", "A"), ("Sahm", "Felix", "F")]),
    dict(pmid="40392954", title="ROBIN: A unified nanopore-based assay integrating intraoperative methylome classification and next-day comprehensive profiling for ultra-rapid tumor diagnosis.",
         journal="Neuro-oncology", iso="Neuro Oncol", year="2025", vol="27", iss="8", pg="2035-2046",
         doi="10.1093/neuonc/noaf103", pmc="PMC12448888",
         authors=[("Deacon", "Simon", "S"), ("Cahyani", "Inswasti", "I"), ("Loose", "Matthew", "M")]),
    dict(pmid="42152226", title="cIMPACT-NOW Update 12: Refining pathology-based risk stratification and grading for IDH-mutant gliomas.",
         journal="Neuro-oncology", iso="Neuro Oncol", year="2026", vol="28", iss="9", pg="2069-2089",
         doi="10.1093/neuonc/noag108", pmc="PMC13550680",
         authors=[("Brat", "Daniel J", "DJ"), ("Aldape", "Kenneth", "K"), ("Figarella-Branger", "Dominique", "D")]),
    dict(pmid="40481322", title="crossNN is an explainable framework for cross-platform DNA methylation-based classification of tumors.",
         journal="Nature cancer", iso="Nat Cancer", year="2025", vol="6", iss="7", pg="1283-94",
         doi="10.1038/s43018-025-00976-5", pmc="PMC12296554",
         authors=[("Yuan", "Dongsheng", "D"), ("Jugas", "Robin", "R"), ("Euskirchen", "Philipp", "P")]),
]
BY_PMID = {a["pmid"]: a for a in ARTICLES}


def article_xml(a):
    au = "".join(f"<Author><LastName>{escape(l)}</LastName><ForeName>{escape(f)}</ForeName><Initials>{i}</Initials></Author>"
                 for l, f, i in a["authors"])
    return f"""<PubmedArticle><MedlineCitation><PMID>{a['pmid']}</PMID><Article>
<Journal><JournalIssue><Volume>{a['vol']}</Volume><Issue>{a['iss']}</Issue><PubDate><Year>{a['year']}</Year></PubDate></JournalIssue>
<Title>{a['journal']}</Title><ISOAbbreviation>{a['iso']}</ISOAbbreviation></Journal>
<ArticleTitle>{a['title']}</ArticleTitle><Pagination><MedlinePgn>{a['pg']}</MedlinePgn></Pagination>
<Abstract><AbstractText>Fixture abstract for {a['pmid']}.</AbstractText></Abstract>
<AuthorList>{au}</AuthorList><Language>eng</Language></Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="pubmed">{a['pmid']}</ArticleId><ArticleId IdType="doi">{a['doi']}</ArticleId>
<ArticleId IdType="pmc">{a['pmc']}</ArticleId></ArticleIdList></PubmedData></PubmedArticle>"""


def _plain(t):
    return re.sub(r"<[^>]+>", "", t).lower()


class MockClient(PubMedClient):
    def __init__(self):
        super().__init__("test@example.org")
        self.calls = []

    def get(self, endpoint, params, retries=4):
        self.calls.append((endpoint, params.get("term") or params.get("bdata") or params.get("id")))
        if endpoint == "ecitmatch.cgi":
            j, y, v, p, au = params["bdata"].split("|")[:5]
            hit = [a for a in ARTICLES if a["iso"].lower() == j.lower() and a["year"] == y
                   and a["pg"].split("-")[0].lower() == p.lower()]
            return params["bdata"] + (hit[0]["pmid"] if hit else "NOT_FOUND")
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
            return ("<eSearchResult><Count>%d</Count><IdList>%s</IdList></eSearchResult>"
                    % (len(ids), "".join(f"<Id>{i}</Id>" for i in ids)))
        if endpoint == "efetch.fcgi":
            body = "".join(article_xml(BY_PMID[i]) for i in params["id"].split(",") if i in BY_PMID)
            return f"<PubmedArticleSet>{body}</PubmedArticleSet>"
        raise AssertionError(endpoint)
