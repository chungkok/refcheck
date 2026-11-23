"""RefVerify classification: one test per integrity status."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from fixtures import ARTICLES, MockClient  # noqa: E402
from refcheck.extract import parse_reference_text  # noqa: E402
from refcheck.pipeline import default_decisions, run_matching  # noqa: E402
from refcheck.verify import STATUSES, classify_all, find_duplicates, source_type  # noqa: E402

ROBIN = ("Deacon S, Cahyani I, et al. ROBIN: A unified nanopore-based assay integrating intraoperative "
         "methylome classification and next-day comprehensive profiling for ultra-rapid tumor diagnosis. "
         "Neuro Oncol. {year};{vol}(8):2035-2046.{tail}")


def run(texts, articles=None, decisions_override=None, reviewed=None):
    refs = []
    for i, t in enumerate(texts, 1):
        r = parse_reference_text(t)
        r["num"] = i
        refs.append(r)
    client = MockClient(articles=articles)
    res = run_matching(refs, client)
    dec = default_decisions(refs, res)
    dec.update(decisions_override or {})
    need = {p for p, _ in dec.values() if p} | {p for r in res.values()
                                               for v in (r.get("identifier_hits") or {}).values() for p in v}
    recs = {r["pmid"]: r for r in client.efetch(sorted(need))}
    return classify_all(refs, res, dec, recs, reviewed), res


def status(texts, **kw):
    out, _ = run(texts, **kw)
    return [out[i + 1]["status"] for i in range(len(texts))], out


def test_verified():
    st, out = status([ROBIN.format(year=2025, vol=27, tail="")])
    assert st == ["VERIFIED"] and out[1]["decided_by"] == "automatic"


def test_year_off_by_one_is_warning():
    st, out = status([ROBIN.format(year=2024, vol=27, tail="")])
    assert st == ["VERIFIED_WITH_WARNINGS"]
    assert out[1]["findings"][0]["code"] == "year_off_by_one"


def test_wrong_volume_is_metadata_error():
    st, out = status([ROBIN.format(year=2025, vol=26, tail="")])
    assert st == ["METADATA_ERROR"]
    assert any(f["code"] == "volume_differs" for f in out[1]["findings"])


def test_doi_of_another_paper():
    st, out = status([ROBIN.format(year=2025, vol=27, tail=" doi:10.1093/neuonc/noaf194.")])
    assert st == ["DOI_MISMATCH"]
    f = next(f for f in out[1]["findings"] if f["code"] == "doi_mismatch")
    assert "41092086" in f["detail"] and "CBTRUS" in f["detail"]


def test_pmid_of_another_paper_and_missing_pmid():
    st, out = status([ROBIN.format(year=2025, vol=27, tail=" PMID: 41092086."),
                      ROBIN.format(year=2025, vol=27, tail=" PMID: 99999999.")])
    assert st == ["PMID_MISMATCH", "DUPLICATE"] or st[0] == "PMID_MISMATCH"
    assert "CBTRUS" in next(f for f in out[1]["findings"] if f["code"] == "pmid_mismatch")["detail"]
    f2 = next(f for f in out[2]["findings"] if f["code"] == "pmid_not_found")
    assert "does not exist" in f2["detail"] and "40392954" in f2["detail"]
    assert "PMID_MISMATCH" not in out[2]["flags"]          # a PMID that does not exist is not another paper
    assert "METADATA_ERROR" in out[2]["flags"]


def test_retraction_correction_concern():
    base = ARTICLES[2]
    retracted = dict(base, pubtypes=["Journal Article", "Retracted Publication"],
                     links=[("RetractionIn", "Neuro Oncol. 2026", "50000001")])
    corrected = dict(base, links=[("ErratumIn", "Neuro Oncol. 2025", "50000002")])
    concern = dict(base, links=[("ExpressionOfConcernIn", "Neuro Oncol. 2026", "50000003")])
    ref = ROBIN.format(year=2025, vol=27, tail="")
    assert status([ref], articles=[retracted])[0] == ["RETRACTED"]
    st, out = status([ref], articles=[corrected])
    assert st == ["CORRECTED"] and "50000002" in out[1]["findings"][0]["detail"]
    assert status([ref], articles=[concern])[0] == ["EXPRESSION_OF_CONCERN"]


def test_unmatched_sources_are_never_called_fabricated():
    texts = [
        "Halldorsson S, et al. Whole-genome long-read sequencing for rapid diagnostics of brain tumors. "
        "medRxiv. 2026. doi:10.64898/2026.04.23.26351563.",
        "Li H. minimap2 aligner. Available from: https://github.com/lh3/minimap2. Accessed 2025.",
        "Nobody A, Else B. A completely invented title about unicorn genomics in mice. J Imag Res. 2024;3:1-9.",
    ]
    st, out = status(texts)
    assert st == ["PREPRINT", "NON_PUBMED_SOURCE", "NOT_FOUND_IN_PUBMED"]
    text = repr(out).lower()
    assert "fabricat" not in text and "fake" not in text and "hallucinat" not in text


def test_unconfirmed_candidate_is_unresolved_until_user_decides():
    # first author wrong: matcher abstains (REVIEW); verify reports UNRESOLVED
    ref = ROBIN.format(year=2025, vol=27, tail="").replace("Deacon S, Cahyani I", "Smith Q, Jones R")
    st, out = status([ref])
    assert st == ["UNRESOLVED"] and "first author differs" in out[1]["findings"][0]["detail"]
    # user accepts the suggestion: identity is confirmed, author error reported
    st, out = status([ref], decisions_override={1: ("40392954", "MANUAL")})
    assert st == ["METADATA_ERROR"] and out[1]["decided_by"] == "user"


def test_check_error():
    import requests
    refs = [parse_reference_text(ROBIN.format(year=2025, vol=27, tail=""))]
    refs[0]["num"] = 1
    c = MockClient(fail={k: [requests.ConnectionError()] * 300 for k in ("esearch.fcgi", "ecitmatch.cgi")})
    import refcheck.pubmed as pm
    sleep, pm.time.sleep = pm.time.sleep, lambda s: None
    try:
        res = run_matching(refs, c)
    finally:
        pm.time.sleep = sleep
    out = classify_all(refs, res, default_decisions(refs, res), {})
    assert out[1]["status"] == "CHECK_ERROR"


def test_duplicates_by_pmid_and_title():
    refs = [dict(num=1, title="A long enough title about myeloma genomics", year="2020"),
            dict(num=2, title="Something else entirely about leukaemia", year="2021"),
            dict(num=3, title="A long enough title about myeloma genomics.", year="2020"),
            dict(num=4, title="x", year="")]
    dec = {1: ("", "NO_MATCH"), 2: ("123", "AUTO_MATCH"), 3: ("", "NO_MATCH"), 4: ("123", "AUTO_MATCH")}
    assert find_duplicates(refs, dec) == {3: 1, 4: 2}


def test_source_type_heuristics():
    assert source_type(dict(text="Smith J. Title. bioRxiv. 2024.", doi="", journal="bioRxiv"))[0] == "preprint"
    assert source_type(dict(text="WHO. Classification of tumours. 5th ed. Lyon: IARC Press; 2021.",
                            doi="", journal=""))[0] == "non_pubmed"
    assert source_type(dict(text="Smith J. Title. Blood. 2020;1:1.", doi="", journal="Blood"))[0] == "journal"


def test_every_status_has_help_text():
    from refcheck.verify import STATUS_HELP
    assert set(STATUS_HELP) == set(STATUSES)


def test_templated_titles_differing_by_gene_are_not_duplicates():
    # held out ChatGPT set: near identical titles that name different genes
    refs = [dict(num=1, title="Ferroptosis susceptibility linked to ATM expression in triple negative breast cancer",
                 year="2012"),
            dict(num=2, title="Ferroptosis susceptibility linked to TP53 expression in triple negative breast cancer",
                 year="2012"),
            dict(num=3, title="Measurable residual disease assessment using IDH2 targeted sequencing in leukaemia",
                 year="2020"),
            dict(num=4, title="Measurable residual disease assessment using IDH1 targeted sequencing in leukaemia",
                 year="2020"),
            dict(num=5, title="Ferroptosis susceptibility linked to ATM expression in triple negative breast cancer.",
                 year="2012")]
    dec = {n: ("", "NO_MATCH") for n in range(1, 6)}
    assert find_duplicates(refs, dec) == {5: 1}


def test_distinctive_tokens_ignore_case_style():
    from refcheck.verify import distinctive_tokens
    assert distinctive_tokens("TP53 loss in AML") == {"tp53", "aml"}
    assert distinctive_tokens("TP53 LOSS IN AML") == {"tp53"}          # all caps: digits only
    assert distinctive_tokens("tp53 loss in aml") == {"tp53"}


def test_repeated_title_with_different_year_is_a_minor_prompt():
    from refcheck.verify import find_repeated_titles
    t = "Prognostic impact of CCND1 alterations in newly diagnosed glioblastoma: a multicentre cohort study"
    refs = [dict(num=1, title=t, year="2012"), dict(num=2, title=t, year="2021"),
            dict(num=3, title=t, year="2012")]
    dec = {n: ("", "NO_MATCH") for n in range(1, 4)}
    dups = find_duplicates(refs, dec)
    assert dups == {3: 1}
    assert find_repeated_titles(refs, dec, dups) == {2: 1}
    # both resolved to different PubMed records: two real works, no prompt
    dec2 = {1: ("111", "AUTO_MATCH"), 2: ("222", "AUTO_MATCH"), 3: ("", "NO_MATCH")}
    assert 2 not in find_repeated_titles(refs, dec2, find_duplicates(refs, dec2))


def test_repeated_title_finding_reaches_the_report():
    t = "Prognostic impact of CCND1 alterations in newly diagnosed glioblastoma: a multicentre cohort study"
    refs = [dict(num=1, title=t, year="2012", text=t, authors="Smith J", journal="Blood"),
            dict(num=2, title=t, year="2021", text=t, authors="Jones K", journal="Leukemia")]
    out = classify_all(refs, {}, {1: ("", "NO_MATCH"), 2: ("", "NO_MATCH")}, {})
    codes = {f["code"]: f["severity"] for f in out[2]["findings"]}
    assert codes.get("title_repeated") == "minor"
    assert "duplicate" not in codes and out[2]["status"] != "DUPLICATE"


def test_swapped_disease_word_is_a_different_title():
    from refcheck.verify import find_repeated_titles
    a = "A machine learning classifier integrating NPM1 status and transcriptomic features for risk stratification in melanoma"
    b = a.replace("melanoma", "glioblastoma")
    c = a.replace("stratification", "stratifcation")             # typo: still the same title
    refs = [dict(num=1, title=a, year="2012"), dict(num=2, title=b, year="2013"), dict(num=3, title=c, year="2012")]
    dec = {n: ("", "NO_MATCH") for n in range(1, 4)}
    dups = find_duplicates(refs, dec)
    assert dups == {3: 1}
    assert find_repeated_titles(refs, dec, dups) == {}
