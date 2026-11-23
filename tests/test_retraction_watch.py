"""Retraction Watch lookups on rows in the published format (checked against the
dataset downloaded on 21 November 2026: 73,965 records, same 20 columns plus a trailing
empty one)."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from refcheck.extract import parse_reference_text  # noqa: E402
from refcheck.sources import Evidence, JSONService, RetractionWatchIndex  # noqa: E402
from refcheck.verify import classify_reference  # noqa: E402

HEADER = ("Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,ArticleType,"
          "RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,OriginalPaperDOI,"
          "OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes,\n")
T1 = "Inhibition of tumour growth by a novel kinase antagonist in mice"
T2 = "Hepatic regeneration after partial hepatectomy in aged rats revisited"
T3 = "Cardiac stem cells repair infarcted myocardium in a porcine model"
T4 = "A ferroptosis signature predicts outcome in multiple myeloma cohorts"
ROWS = [
    f'1,{T1},,,Oncogene,P,AU,Jane Smith;Li Wei,,Research Article;,3/5/2019 0:00,10.1/n1,900,'
    f'1/28/2017 0:00,10.1038/onc.2017.1,111,Retraction,+Falsification/Fabrication of Data;,No,,',
    f'2,{T2},,,Hepatology,P,US,Takara R Newsome-Cuby;K Darko,,Research Article;,2/2/2021 0:00,10.1/n2,0,'
    f'1/1/2020 0:00,unavailable,0,Retraction,+Error in Data;,No,,',
    f'3,{T3},,,Circulation,P,US,Ann Lee,,Research Article;,2/2/2021 0:00,10.1/n3,0,'
    f'1/1/2019 0:00,10.1161/circ.3,0,Expression of concern,Investigation;,No,,',
    f'4,{T3},,,Circulation,P,US,Ann Lee,,Research Article;,7/12/2021 0:00,10.1/n4,0,'
    f'1/1/2019 0:00,10.1161/circ.3,0,Reinstatement,Upgrade/Update of Prior Notice(s);,No,,',
    f'5,{T4},,,Blood,P,AU,Bo Chen,,Research Article;,8/23/2023 0:00,10.1/n5,0,'
    f'1/1/2022 0:00,10.1182/blood.5,0,Expression of concern,Concerns;Updated to Retraction;,No,,',
]


def index(tmp_path):
    p = tmp_path / "retraction_watch.csv"
    p.write_text(HEADER + "\n".join(ROWS) + "\n", encoding="utf-8")
    return RetractionWatchIndex(str(p))


def check(idx, text, rec=None):
    ref = parse_reference_text(text)
    ref["num"] = 1
    ev = Evidence(JSONService("t@example.org"), None, [], idx).for_reference(ref, rec)
    pmid = (rec or {}).get("pmid", "")
    return classify_reference(ref, {"status": "AUTO_MATCH" if rec else "NO_MATCH", "methods_run": [],
                                    "identifier_hits": {}}, pmid, "automatic",
                              {pmid: rec} if rec else {}, None, ev, None)


def codes(out):
    return {f["code"] for f in out["findings"]}


def test_written_doi_in_any_form_finds_the_retraction(tmp_path):
    idx = index(tmp_path)
    for doi in ("doi:10.1038/onc.2017.1", "https://doi.org/10.1038/ONC.2017.1"):
        out = check(idx, f"Smith J, Wei L. {T1}. Oncogene. 2017;36:1-10. {doi}")
        assert "RETRACTED" in out["flags"]
        f = [x for x in out["findings"] if x["code"] == "retracted"][0]
        assert f["severity"] == "critical" and "Falsification" in f["detail"] and "record 1" in f["source"]


def test_pubmed_match_by_pmid_finds_the_retraction(tmp_path):
    rec = dict(pmid="111", doi="", title=T1, authors=[], journal="Oncogene", year="2017",
               publication_types=["Journal Article"])
    out = check(index(tmp_path), f"Smith J, Wei L. {T1}. Oncogene. 2017;36:1-10.", rec)
    assert "RETRACTED" in out["flags"]


def test_no_identifier_matches_by_title_and_first_author(tmp_path):
    idx = index(tmp_path)
    out = check(idx, f"Newsome-Cuby TR, Darko K. {T2}. Hepatology. 2020;71:1-9.")
    assert "RETRACTED" in out["flags"]
    assert "matched by title and first author" in [x for x in out["findings"] if x["code"] == "retracted"][0]["source"]
    # same title, another first author, or a year far off: not this paper
    assert "retracted" not in codes(check(idx, f"Brown P, Darko K. {T2}. Hepatology. 2020;71:1-9."))
    assert "retracted" not in codes(check(idx, f"Newsome-Cuby TR. {T2}. Hepatology. 2009;71:1-9."))


def test_a_later_reinstatement_supersedes_the_concern(tmp_path):
    out = check(index(tmp_path), f"Lee A. {T3}. Circulation. 2019;1:1-9. doi:10.1161/circ.3")
    assert "EXPRESSION_OF_CONCERN" not in out["flags"]
    assert any(f["code"] == "reinstated" and "later reinstated" in f["detail"] for f in out["findings"])


def test_a_notice_updated_to_retraction_counts_as_retracted(tmp_path):
    out = check(index(tmp_path), f"Chen B. {T4}. Blood. 2022;1:1-9. doi:10.1182/blood.5")
    assert "RETRACTED" in out["flags"]


def test_clean_reference_is_not_flagged(tmp_path):
    out = check(index(tmp_path), "Kok CH, Yeung DT. A different paper entirely about CML. Blood. 2020;1:1. "
                                 "doi:10.1182/blood.999")
    assert not codes(out) & {"retracted", "expression_of_concern", "reinstated"}


STYLED_T2 = {
    "Vancouver": f"Newsome-Cuby TR, Darko K. {T2}. Hepatology. 2020;71(2):1-9.",
    "APA": f"Newsome-Cuby, T. R., & Darko, K. (2020). {T2}. Hepatology, 71(2), 1–9.",
    "Nature": f"Newsome-Cuby, T. R. & Darko, K. {T2}. Hepatology 71, 1–9 (2020).",
    "Science": f"T. R. Newsome-Cuby, K. Darko, {T2}. Hepatology 71, 1–9 (2020).",
    "Cell": f"Newsome-Cuby, T.R., and Darko, K. (2020). {T2}. Hepatology 71, 1–9.",
    "Harvard": f"Newsome-Cuby, T.R. and Darko, K. (2020) ‘{T2}’, Hepatology, 71(2), pp. 1–9.",
    "Chicago": f"Newsome-Cuby, Takara R., and K. Darko. 2020. “{T2}.” Hepatology 71 (2): 1–9.",
    "IEEE": f"T. R. Newsome-Cuby and K. Darko, “{T2},” Hepatology, vol. 71, no. 2, pp. 1–9, 2020.",
    "MLA": f"Newsome-Cuby, Takara R., and K. Darko. “{T2}.” Hepatology, vol. 71, no. 2, 2020, pp. 1–9.",
    "MDPI": f"Newsome-Cuby, T.R.; Darko, K. {T2}. Hepatology 2020, 71, 1–9.",
    "Lancet": f"Newsome-Cuby TR, Darko K. {T2}. Hepatology 2020; 71: 1–9.",
    "Bioinformatics before 2023": f"Newsome-Cuby,T.R. and Darko,K. (2020) {T2}. Hepatology, 71, 1–9.",
}


import pytest  # noqa: E402


@pytest.mark.parametrize("style", sorted(STYLED_T2))
def test_retraction_without_identifiers_in_every_style(tmp_path, style):
    out = check(index(tmp_path), STYLED_T2[style])
    assert "RETRACTED" in out["flags"], style
