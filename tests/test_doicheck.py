"""Does the written DOI belong to the cited work? Versions, notices, typos, rewording,
and the pattern 'DOI of another work, cited work found nowhere'."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from fixtures import BY_PMID, article_xml  # noqa: E402
from test_verify import ROBIN, run  # noqa: E402
from refcheck.doicheck import NOTICE_PREFIX, compare, from_crossref, likely_typo, verdict, claimed_from  # noqa: E402
from refcheck.extract import parse_reference_text  # noqa: E402
from refcheck.pubmed import parse_pubmed_xml  # noqa: E402
from refcheck.sources import parse_crossref_work  # noqa: E402
from refcheck.verify import classify_reference  # noqa: E402

ROBIN_DOI = "10.1093/neuonc/noaf103"
ROBIN_TITLE = ("ROBIN: A unified nanopore-based assay integrating intraoperative methylome classification and "
               "next-day comprehensive profiling for ultra-rapid tumor diagnosis")


def rec(pmid):
    return parse_pubmed_xml(f"<PubmedArticleSet>{article_xml(BY_PMID[pmid])}</PubmedArticleSet>")[0]


def cw(doi, title, family="Smith", journal="Some Journal", year=2021, typ="journal-article", **extra):
    w = {"DOI": doi, "title": [title], "container-title": [journal], "type": typ,
         "issued": {"date-parts": [[year]]}, "author": [{"family": family}]}
    w.update(extra)
    return parse_crossref_work(w)


def matched_robin(written_doi, wd, crossref_record=None):
    ref = parse_reference_text(ROBIN.format(year=2025, vol=27, tail=f" doi:{written_doi}"))
    ref["num"] = 1
    r = rec("40392954")
    ev = {"crossref_written_doi": wd}
    if crossref_record:
        ev["crossref_record"] = crossref_record
    return classify_reference(ref, {"status": "AUTO_MATCH"}, "40392954", "automatic", {"40392954": r},
                              evidence=ev)


def codes(it):
    return {f["code"] for f in it["findings"]}


def test_linked_preprint_is_not_a_mismatch():
    wd = cw("10.1101/2024.05.01.590001", "ROBIN: real time nanopore methylome classification during surgery",
            "Deacon", "bioRxiv", 2024, "posted-content",
            relation={"is-preprint-of": [{"id-type": "doi", "id": ROBIN_DOI, "asserted-by": "subject"}]})
    it = matched_robin("10.1101/2024.05.01.590001", wd)
    assert "DOI_MISMATCH" not in it["flags"] and it["status"] == "VERIFIED"
    f = next(f for f in it["findings"] if f["code"] == "doi_is_related_version")
    assert f["severity"] == "info" and "linked version" in f["detail"]


def test_unlinked_preprint_by_same_author_is_a_warning_not_a_mismatch():
    wd = cw("10.1101/2024.05.01.590001", "Nanopore methylome classification during surgery", "Deacon",
            "bioRxiv", 2024, "posted-content")
    it = matched_robin("10.1101/2024.05.01.590001", wd)
    assert "DOI_MISMATCH" not in it["flags"] and it["status"] == "VERIFIED_WITH_WARNINGS"
    assert next(f for f in it["findings"] if f["code"] == "doi_is_related_version")["severity"] == "minor"


def test_preprint_by_another_author_is_a_mismatch():
    wd = cw("10.1101/2024.05.01.590001", "Spatial transcriptomics of the mouse cortex", "Zhang", "bioRxiv", 2024,
            "posted-content")
    assert matched_robin("10.1101/2024.05.01.590001", wd)["status"] == "DOI_MISMATCH"


def test_correction_notice_doi():
    wd = cw("10.1093/neuonc/noaf999", "Correction to: " + ROBIN_TITLE, "", "Neuro-Oncology", 2025,
            **{"update-to": [{"type": "correction", "DOI": ROBIN_DOI, "source": "publisher"}]})
    it = matched_robin("10.1093/neuonc/noaf999", wd)
    assert "DOI_MISMATCH" not in it["flags"] and "CORRECTED" not in it["flags"]
    f = next(f for f in it["findings"] if f["code"] == "doi_is_notice")
    assert ROBIN_DOI in f["detail"] and it["status"] == "VERIFIED_WITH_WARNINGS"


def test_notices_of_another_works_doi_do_not_apply():
    wd = cw("10.5555/terns", "Seasonal migration of Arctic terns", "Egevang", "Ornithology", 2019,
            **{"update-to": [{"type": "retraction", "DOI": "10.5555/notice", "source": "retraction-watch"}]})
    it = matched_robin("10.5555/terns", wd)
    assert it["status"] == "DOI_MISMATCH" and "RETRACTED" not in it["flags"]
    assert "title, first author, year, journal differ" in next(
        f["detail"] for f in it["findings"] if f["code"] == "doi_mismatch")


def test_typo_hint_and_alternative_doi():
    wd = cw("10.1093/neuonc/noaf193", "An unrelated glioma cohort study", "Other", "Neuro-Oncology", 2025)
    it = matched_robin("10.1093/neuonc/noaf193", wd)
    assert it["status"] == "DOI_MISMATCH" and "doi_typo_likely" in codes(it)
    assert ROBIN_DOI in next(f["detail"] for f in it["findings"] if f["code"] == "doi_typo_likely")
    same = cw("10.9999/robin.mirror", ROBIN_TITLE, "Deacon", "Neuro Oncol", 2025)
    it2 = matched_robin("10.9999/robin.mirror", same)
    assert "doi_alternative" in codes(it2) and it2["status"] == "VERIFIED"
    assert likely_typo("10.1/abc123", "10.1/abc124") and not likely_typo("10.1/abc123", "10.2/abc123")
    assert not likely_typo("10.1/abc", "10.1/xyzuvw")


def _unmatched(text, wd, searched=("crossref", "openalex")):
    ref = parse_reference_text(text)
    ref["num"] = 1
    ev = {"crossref_written_doi": wd, "searched_elsewhere": list(searched), "elsewhere": None}
    return classify_reference(ref, {"status": "NO_MATCH", "methods_run": ["title-words"]}, "", "automatic", {},
                              evidence=ev)


def test_reworded_title_is_a_warning():
    wd = cw("10.5555/kary", "Deep learning classification of karyotype images in clinical cytogenetics", "Smith",
            "Cytogenet Res", 2021)
    it = _unmatched("Smith J, Jones K. Deep learning for karyotype images. Cytogenet Res. 2021;3:10-20. "
                    "doi:10.5555/kary", wd)
    assert "DOI_MISMATCH" not in it["flags"] and "doi_title_differs" in codes(it)
    assert "not_found_in_any_source" not in codes(it)


def test_fabrication_pattern_is_named_but_not_called_fabricated():
    wd = cw("10.5555/terns", "Seasonal migration of Arctic terns", "Egevang", "Ornithology", 2019)
    it = _unmatched("Ghost B, Phantom C. Imaginary biomarkers of glioma progression. J Imag Res. 2021;4:11-19. "
                    "doi:10.5555/terns", wd)
    assert it["status"] == "DOI_MISMATCH"
    assert {"doi_mismatch", "not_found_in_any_source", "doi_other_work_cited_not_found"} <= codes(it)
    assert "fabricat" not in str(it).lower()


def test_fabrication_pattern_with_pubmed_only():
    out, _ = run(["Ghost B, Phantom C. Imaginary biomarkers of glioma progression in a fictional cohort. "
                   "J Imag Res. 2021;4(2):11-19. doi:10.1093/neuonc/noaf194."])
    it = out[1]
    assert it["status"] == "DOI_MISMATCH", it
    assert "doi_other_work_cited_not_found" in codes(it)
    assert "PubMed" in next(f["detail"] for f in it["findings"] if f["code"] == "doi_other_work_cited_not_found")


def test_comparison_rules():
    claimed = claimed_from(parse_reference_text(ROBIN.format(year=2025, vol=27, tail="")))
    other = from_crossref(cw("10.1/x", "A different title entirely about birds", "Deacon", "Neuro Oncol", 2025))
    c = compare(claimed, other)
    assert c["title"] is False and c["first_author"] and c["year"] and c["journal"]
    assert verdict(c) == "different"          # same group, same journal, same year: still another paper
    sub = from_crossref(cw("10.1/y", ROBIN_TITLE.split(":")[0] + ": a unified nanopore based assay integrating "
                           "intraoperative methylome classification", "Deacon", "Neuro Oncol", 2025))
    assert verdict(compare(claimed, sub)) in ("same", "reworded")
    assert not NOTICE_PREFIX.match("Correction of scoliosis in adolescents")
    assert NOTICE_PREFIX.match("Retraction Note: Something") and NOTICE_PREFIX.match("Erratum to \"x\"")
