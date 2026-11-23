"""Secondary sources: Crossref, OpenAlex, doi.org, NLM Catalog, DOAJ, Unpaywall and a
local Retraction Watch file. The network is mocked with answers shaped like the
services' documented JSON; nothing here reaches the internet."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import requests  # noqa: E402

from fixtures import ARTICLES, MockClient  # noqa: E402
from refcheck.config import Settings, load_settings  # noqa: E402
from refcheck.extract import extract_references, parse_reference_text  # noqa: E402
from refcheck.pipeline import build_outputs, default_decisions, run_matching  # noqa: E402
from refcheck.sources import (Evidence, JSONService, RetractionWatchIndex, crossref_updates,  # noqa: E402
                              evidence_from_settings, issn_valid, issns_in_text)

RETRACTED_DOI = "10.1007/s00401-022-02415-6"       # fixture PMID 35357562 (fictional retraction)
CONCERN_DOI = "10.1038/s43018-025-00976-5"         # fixture PMID 40481322 (in the RW file below)
OA_DOI = "10.1093/neuonc/noaf103"                  # fixture PMID 40392954


def cr_work(doi, title, journal="Some Journal", year=2021, typ="journal-article", issn=None, **extra):
    w = {"DOI": doi, "title": [title], "container-title": [journal], "type": typ,
         "issued": {"date-parts": [[year]]}, "author": [{"family": "Smith", "given": "J"}],
         "ISSN": issn or []}
    w.update(extra)
    return {"status": "ok", "message-type": "work", "message": w}


class MockService(JSONService):
    """Routes URLs to canned JSON. Unknown paths answer 404."""

    def __init__(self, routes, **kw):
        super().__init__("test@example.org", **kw)
        self.routes = routes
        self.urls = []

    def _fetch(self, url, params):
        self.urls.append((url, dict(params)))
        for key, val in self.routes.items():
            if key in url or key in json.dumps(params):
                if isinstance(val, Exception):
                    raise val
                return 200, json.dumps(val)
        return 404, '{"message": "not found"}'


ROUTES = {
    # matched in PubMed, retracted according to Crossref's Retraction Watch annotation
    "/works/" + RETRACTED_DOI: cr_work(RETRACTED_DOI, "Rapid-CNS2: rapid comprehensive adaptive nanopore-sequencing "
                                       "of CNS tumors, a proof-of-concept study", "Acta Neuropathologica", 2022,
                                       **{"update-to": [{"type": "retraction", "DOI": "10.1007/notice1",
                                                         "source": "retraction-watch", "label": "Retraction",
                                                         "record-id": 99}]}),
    # a DOI written in the manuscript that belongs to another paper
    "/works/10.5555/other": cr_work("10.5555/other", "Seasonal migration of Arctic terns", "Ornithology", 2019),
    # preprint found only in Crossref, with its published version
    "Nanopore methylation calling for preprint tumours": {"message": {"items": [
        cr_work("10.1101/2024.01.01.000001", "Nanopore methylation calling for preprint tumours in the clinic",
                "bioRxiv", 2024, "posted-content",
                relation={"is-preprint-of": [{"id-type": "doi", "id": "10.1000/published.1"}]})["message"]]}},
    # a book found only in OpenAlex
    "Principles of Neuropathology": {"results": [{
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/book.1",
        "title": "Principles of Neuropathology and Tumour Diagnosis", "publication_year": 2020,
        "type": "book", "primary_location": {"source": {"display_name": "Springer", "issn": []}},
        "authorships": [{"author": {"display_name": "A Writer"}}], "is_retracted": False,
        "open_access": {"oa_url": None}, "biblio": {}}]},
    # OpenAlex record for the OA paper, and Unpaywall
    "/works/doi:" + OA_DOI: {"doi": "https://doi.org/" + OA_DOI, "title": "ROBIN", "publication_year": 2025,
                             "type": "article", "is_retracted": False, "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/40392954"},
                             "open_access": {"oa_url": "https://example.org/robin.pdf"}, "biblio": {}},
    "/v2/" + OA_DOI: {"is_oa": True, "best_oa_location": {"url": "https://example.org/robin-unpaywall"}},
    # DOAJ lists Neuro-Oncology's ISSN (fictional, for the test)
    "issn%3A1522-8517": {"total": 1, "results": [{"id": "x"}]},
    "issn:1522-8517": {"total": 1, "results": [{"id": "x"}]},
}

REFS = [
    # 1 matched, retracted per Crossref (Retraction Watch)
    "Patel A, Dogan H, von Deimling A, Sahm F. Rapid-CNS2: rapid comprehensive adaptive nanopore-sequencing of CNS "
    "tumors, a proof-of-concept study. Acta Neuropathol. 2022;143(5):609-612. doi:" + RETRACTED_DOI,
    # 2 matched, open access copy, journal in DOAJ
    "Deacon S, Cahyani I, Holmes N, et al. ROBIN: A unified nanopore-based assay integrating intraoperative methylome "
    "classification and next-day comprehensive profiling for ultra-rapid tumor diagnosis. Neuro Oncol. "
    "2025;27(8):2035-2046.",
    # 3 matched, expression of concern in the local Retraction Watch file
    "Yuan D, Jugas R, Euskirchen P. crossNN is an explainable framework for cross-platform DNA methylation-based "
    "classification of tumors. Nat Cancer. 2025;6(7):1283-94.",
    # 4 not in PubMed, preprint in Crossref
    "Lee K, Park J. Nanopore methylation calling for preprint tumours in the clinic. bioRxiv. 2024.",
    # 5 not in PubMed, book in OpenAlex
    "Writer A. Principles of Neuropathology and Tumour Diagnosis. Berlin: Springer; 2020.",
    # 6 not anywhere; written DOI belongs to another paper; invalid ISSN in the text
    "Ghost B, Phantom C. Imaginary biomarkers of glioma progression in a fictional cohort. J Imag Res. "
    "2021;4(2):11-19. ISSN 1234-5678. doi:10.5555/other",
    # 7 not anywhere; DOI not registered
    "Nobody Z. A completely unknown study of nothing. Unknown J. 2020;1:1-2. doi:10.9999/notregistered",
]


def _run(tmp_path, routes=ROUTES, rw=None, enabled=None):
    client = MockClient(articles=[dict(a, issn="1522-8517") if a["iso"] == "Neuro Oncol" else a for a in ARTICLES])
    client.nlm = {"neuro oncol": True, "1522-8517": True, "acta neuropathol": True, "nat cancer": False}
    refs = []
    for i, t in enumerate(REFS, 1):
        r = parse_reference_text(t)
        r["num"] = i
        refs.append(r)
    res = run_matching(refs, client)
    dec = default_decisions(refs, res)
    svc = MockService(routes, audit=client.audit)
    ev = Evidence(svc, client, enabled or ("crossref", "openalex", "doi", "nlmcatalog", "doaj", "unpaywall"),
                  RetractionWatchIndex(rw) if rw else None)
    ex = dict(refs=refs, source_kind="txt", heading_para=None, warnings=[], docx_path=None)
    out = build_outputs(ex, refs, res, dec, client, tmp_path / "out", "t", evidence=ev,
                        report_meta=dict(input_name="t.txt", input_sha256="0", started="now", deployment="test"))
    return out, client, svc


def _rw_csv(tmp_path):
    p = tmp_path / "retraction_watch.csv"
    p.write_text("Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,ArticleType,"
                 "RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,OriginalPaperDOI,"
                 "OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes\n"
                 f'777,crossNN,,,,,,,,,2026-01-02,10.1038/eoc1,0,2025,{CONCERN_DOI.upper()},40481322,'
                 '"Expression of concern","+Concerns/Issues About Data;",No,\n'
                 '778,Other,,,,,,,,,2020-01-01,10.1/x,0,2019,10.1/orig,0,Retraction,"+Fake",No,\n',
                 encoding="utf-8")
    return p


def test_statuses_from_secondary_sources(tmp_path):
    out, client, svc = _run(tmp_path, rw=_rw_csv(tmp_path))
    it = out["integrity"]
    codes = {n: {f["code"] for f in it[n]["findings"]} for n in it}
    st = {n: it[n]["status"] for n in it}

    assert st[1] == "RETRACTED"
    f = next(f for f in it[1]["findings"] if f["code"] == "retracted")
    assert "Retraction Watch" in f["source"] and "10.1007/notice1" in f["detail"]

    assert st[2] in ("VERIFIED", "VERIFIED_WITH_WARNINGS")
    assert "open_access" in codes[2] and "journal_in_doaj" in codes[2]
    assert out["rows"][1]["open_access_url"] == "https://example.org/robin-unpaywall"
    assert out["rows"][1]["suggested_reference"].startswith("Deacon S, Cahyani I, Holmes N, Fox G, Munro R, Wibowo S, "
                                                           "et al. ROBIN")

    assert st[3] == "EXPRESSION_OF_CONCERN"
    assert "journal_not_medline_indexed" in codes[3]
    eoc = next(f for f in it[3]["findings"] if f["code"] == "expression_of_concern")
    assert "record 777" in eoc["source"] and "Concerns" in eoc["detail"]

    assert st[4] == "PREPRINT" and "found_outside_pubmed" in codes[4] and "published_version" in codes[4]
    assert st[5] == "NON_PUBMED_SOURCE" and "found_outside_pubmed" in codes[5]

    assert st[6] == "DOI_MISMATCH"
    assert {"doi_mismatch", "not_found_in_any_source", "issn_invalid"} <= codes[6]
    assert "Arctic terns" in next(f["detail"] for f in it[6]["findings"] if f["code"] == "doi_mismatch")

    assert st[7] == "NOT_FOUND_IN_PUBMED"
    assert {"doi_not_registered", "not_found_in_any_source"} <= codes[7]
    # never called fabricated
    assert not any("fabricat" in json.dumps(x).lower() for x in it.values())

    rep = out["report"]
    assert "crossref" in rep["secondary_sources"]
    assert rep["network"]["summary"]["by_destination"]["api.crossref.org"] >= 1
    assert out["paths"]["bib"].read_text().count("@") == 7


def test_transmitted_values_are_bibliographic(tmp_path):
    out, client, svc = _run(tmp_path)
    sent = " ".join(client.audit.transmitted_values())
    assert "test@example.org" not in sent
    for ref in REFS:                   # no whole reference text goes out
        assert ref not in sent
    hosts = {r.host for r in client.audit.requests}
    assert hosts <= {"eutils.ncbi.nlm.nih.gov", "api.crossref.org", "api.openalex.org", "doi.org", "doaj.org",
                     "api.unpaywall.org"}
    # unmatched titles go to Crossref and OpenAlex only; doi.org, DOAJ, Unpaywall see identifiers only
    for r in client.audit.requests:
        if r.host in ("doi.org", "doaj.org", "api.unpaywall.org"):
            assert all(k in ("email",) for k in r.transmitted), r


def test_disabled_sources_are_not_contacted(tmp_path):
    out, client, svc = _run(tmp_path, enabled=("nlmcatalog",))
    assert not svc.urls
    st = {n: out["integrity"][n]["status"] for n in out["integrity"]}
    assert st[1] != "RETRACTED"          # nothing tells us without Crossref
    assert "found_outside_pubmed" not in {f["code"] for f in out["integrity"][4]["findings"]}


def test_unreachable_service_is_skipped_and_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("refcheck.sources.time.sleep", lambda s: None)
    routes = {"api.": requests.ConnectionError("down"), "doi.org": requests.ConnectionError("down")}
    out, client, svc = _run(tmp_path, routes=routes)
    by_host = {}
    for u, _ in svc.urls:
        h = u.split("/")[2]
        by_host[h] = by_host.get(h, 0) + 1
    assert all(n <= 2 for n in by_host.values()), by_host       # two tries, then the host is skipped
    assert any("secondary source lookups failed" in w for w in out["warnings"])
    # a source that was not reached is not reported as having no match
    assert all("not_found_in_any_source" not in {f["code"] for f in v["findings"]}
               for v in out["integrity"].values())


def test_crossref_update_rules():
    rw = {"title": ["A study"], "update-to": [{"type": "retraction", "DOI": "10.1/n", "source": "retraction-watch"}]}
    assert crossref_updates(rw)[0]["kind"] == "retracted"
    notice = {"title": ["Retraction note: A study"], "update-to": [{"type": "retraction", "DOI": "10.1/orig",
                                                                      "source": "publisher"}]}
    assert crossref_updates(notice)[0]["kind"] == "is_notice"
    by = {"title": ["A study"], "updated-by": [{"type": "correction", "DOI": "10.1/c", "source": "publisher"}]}
    assert crossref_updates(by)[0]["kind"] == "corrected"
    assert crossref_updates({"title": ["x"], "update-to": [{"type": "new_dataset"}]}) == []


def test_issn_checks():
    assert issn_valid("0028-0836") and issn_valid("1522-8517")
    assert issn_valid("1522-8517") and not issn_valid("1234-5678") and issn_valid("1234-5679")
    assert issns_in_text("ISSN 1522-8517; eISSN 15235866. 1999-2001") == ["1522-8517", "1523-5866"]


def test_retraction_watch_index(tmp_path):
    rw = RetractionWatchIndex(_rw_csv(tmp_path))
    hit = rw.lookup(CONCERN_DOI)
    assert hit and hit[0]["nature"] == "Expression of concern" and hit[0]["record_id"] == "777"
    assert rw.lookup(pmid="40481322") == hit
    assert rw.lookup("10.1/none") == []
    assert RetractionWatchIndex.kind("Reinstatement") == "reinstated"


def test_cache_answers_second_run(tmp_path):
    from refcheck.netaudit import DiskCache
    cache = DiskCache(str(tmp_path / "c.sqlite"))
    s1 = MockService(ROUTES, cache=cache)
    Evidence(s1).crossref_doi(RETRACTED_DOI)
    Evidence(s1).doi_registered("10.9999/x")          # a 404 is cached too
    s2 = MockService({}, cache=cache, offline=True)
    assert Evidence(s2).crossref_doi(RETRACTED_DOI)["updates"][0]["kind"] == "retracted"
    assert Evidence(s2).doi_registered("10.9999/x") is False
    assert not s2.urls


def test_settings_and_factory(tmp_path):
    assert load_settings(env={"REFCHECK_SOURCES": "none"}).sources == []
    assert load_settings(env={"REFCHECK_SOURCES": "Crossref, doaj"}).sources == ["crossref", "doaj"]
    assert "unpaywall" in Settings().sources
    assert evidence_from_settings(Settings(sources=[]), MockClient()) is None
    ev = evidence_from_settings(Settings(sources=["crossref", "bogus"], retraction_watch_csv=str(_rw_csv(tmp_path))),
                                MockClient())
    assert ev.enabled == {"crossref"} and ev.rw is not None


# ------------------------------------------------------------ LaTeX input --

BIB = r"""
@string{neuro = "Neuro Oncol"}
@comment{ignored}
@article{deacon2025,
  author  = {Deacon, Simon and Cahyani, Inswasti and Loose, Matthew and others},
  title   = {{ROBIN}: A unified nanopore-based assay integrating intraoperative methylome classification and next-day comprehensive profiling for ultra-rapid tumor diagnosis},
  journal = neuro,
  year    = 2025, volume = {27}, number = {8}, pages = {2035--2046},
  doi     = {https://doi.org/10.1093/neuonc/noaf103}
}
@article{who,
  author = {{World Health Organization}},
  title = "Caf\'{e} and na\"ive M{\"u}ller studies",
  journal = {Bull World Health Organ}, year = {2020}, pmid = {123}
}
@book{vanhelsing, author = {Abraham van Helsing}, title = {Notes}, publisher = {Self}, year = 1897}
"""


def test_bibtex_input(tmp_path):
    p = tmp_path / "refs.bib"
    p.write_text(BIB, encoding="utf-8")
    ex = extract_references(p)
    r = ex["refs"]
    assert ex["source_kind"] == "bibtex" and len(r) == 3
    assert r[0]["authors"] == "Deacon S, Cahyani I, Loose M, et al"
    assert r[0]["title"].startswith("ROBIN: A unified") and r[0]["journal"] == "Neuro Oncol"
    assert (r[0]["year"], r[0]["volume"], r[0]["issue"], r[0]["pages"]) == ("2025", "27", "8", "2035-2046")
    assert r[0]["doi"] == "10.1093/neuonc/noaf103" and r[0]["bibtex_key"] == "deacon2025"
    assert r[1]["authors"] == "World Health Organization" and r[1]["title"] == "Café and naïve Müller studies"
    assert r[1]["pmid_hint"] == "123"
    assert r[2]["authors"] == "van Helsing A"
    # the BibTeX record is matched like any other reference
    res = run_matching(r[:1], MockClient())
    assert res[1]["status"] == "AUTO_MATCH" and res[1]["best"]["pmid"] == "40392954"


def test_bbl_and_tex_input(tmp_path):
    bbl = (r"\begin{thebibliography}{9}" "\n"
           r"\bibitem{patel} Patel A, Dogan H, von Deimling A, Sahm F. Rapid-CNS2: rapid comprehensive adaptive "
           r"nanopore-sequencing of {CNS} tumors, a proof-of-concept study. \newblock \emph{Acta Neuropathol}. "
           r"2022;143(5):609--612." "\n"
           r"\bibitem[Yuan et~al.(2025)]{yuan} Yuan D, Jugas R. crossNN. Nat Cancer. 2025;6(7):1283--94." "\n"
           r"\end{thebibliography}")
    (tmp_path / "a.bbl").write_text(bbl, encoding="utf-8")
    ex = extract_references(tmp_path / "a.bbl")
    assert [x["bibtex_key"] for x in ex["refs"]] == ["patel", "yuan"]
    assert ex["refs"][0]["journal"] == "Acta Neuropathol" and ex["refs"][0]["pages"] == "609-612"
    (tmp_path / "b.tex").write_text(r"\documentclass{article}\begin{document}x\bibliography{mine,other}\end{document}")
    with pytest.raises(ValueError, match="mine, other"):
        extract_references(tmp_path / "b.tex")


def test_bibtex_export_round_trips(tmp_path):
    from refcheck.latexbib import parse_bibtex
    out, client, svc = _run(tmp_path, enabled=())
    entries = parse_bibtex(Path(out["paths"]["bib"]).read_text(encoding="utf-8"))
    assert len(entries) == 7 and len({e["ID"] for e in entries}) == 7
    first = entries[0]
    assert first["pmid"] == "35357562" and first["pages"] == "609--612" and "Patel, Areeba" in first["author"]


def test_cli_source_flags(tmp_path, monkeypatch, capsys):
    import refcheck_cli
    with pytest.raises(SystemExit, match="unknown source"):
        refcheck_cli.main([str(tmp_path / "x.txt"), "--email", "a@b.c", "--sources", "crossref,scopus"])
