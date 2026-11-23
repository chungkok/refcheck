"""Checks added after the 800 case cancer benchmark: title wording, author lists,
initials, PMIDs that do not exist, incomplete references, the DOI lookup after an early
match, the hybrid veto, and parsing of real world author and DOI forms."""
import pytest
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from test_verify import ROBIN, status  # noqa: E402
from refcheck.extract import parse_reference_text  # noqa: E402
from refcheck.textutil import author_list, first_author_surname, split_author  # noqa: E402

TITLE = ("ROBIN: A unified nanopore-based assay integrating intraoperative methylome classification and "
         "next-day comprehensive profiling for ultra-rapid tumor diagnosis")


def robin(authors="Deacon S, Cahyani I, Holmes N, et al", title=TITLE, tail=""):
    return f"{authors}. {title}. Neuro Oncol. 2025;27(8):2035-2046.{tail}"


def codes(out, n=1):
    return {f["code"] for f in out[n]["findings"]}


def test_one_word_title_change_is_reported():
    st, out = status([robin(title=TITLE.replace("tumor diagnosis", "lymphoma diagnosis"))])
    f = next(f for f in out[1]["findings"] if f["code"] == "title_wording_differs")
    assert "lymphoma" in f["detail"] and "tumor" in f["detail"]
    assert st[0] == "VERIFIED_WITH_WARNINGS"


def test_word_order_change_is_reported_even_around_stopwords():
    swapped = TITLE.replace("profiling for ultra-rapid", "for profiling ultra-rapid")
    st, out = status([robin(title=swapped)])
    assert "title_word_order_differs" in codes(out)


def test_spelling_variants_are_not_title_changes():
    st, out = status([robin(title=TITLE.replace("tumor", "tumour"))])
    assert st == ["VERIFIED"]


def test_initials_order_and_missing_authors():
    st, out = status([robin(authors="Deacon J, Cahyani I, Holmes N, et al")])
    assert "first_author_initials_differ" in codes(out) and st[0] == "VERIFIED_WITH_WARNINGS"
    st, out = status([robin(authors="Deacon S, Holmes N, Cahyani I, et al")])
    assert "author_order_differs" in codes(out)
    st, out = status([robin(authors="Deacon S, Cahyani I, Invented Q, et al")])
    assert "authors_not_in_record" in codes(out) and st[0] == "METADATA_ERROR"
    # first author dropped, no identifier: the list starts with PubMed's second author, so the paper is
    # identified (title, journal, volume and page agree) and the error is reported, not left unresolved
    st, out = status([robin(authors="Cahyani I, Holmes N, et al")])
    assert out[1]["pmid"] == "40392954" and st[0] == "METADATA_ERROR"
    assert {"first_author_differs", "authors_omitted"} <= codes(out)


def test_written_pmid_that_does_not_exist():
    st, out = status([robin(tail=" PMID: 49999999.")])
    f = next(f for f in out[1]["findings"] if f["code"] == "pmid_not_found")
    assert "does not exist" in f["detail"] and "40392954" in f["detail"]
    assert "PMID_MISMATCH" not in out[1]["flags"] and st[0] == "METADATA_ERROR"


def test_doi_is_checked_even_when_the_pmid_settled_the_match():
    # the written PMID matches at once; the DOI of another paper must still be looked up
    st, out = status([robin(tail=" doi:10.1093/neuonc/noaf194. PMID: 40392954.")])
    assert "doi_mismatch" in codes(out) and "CBTRUS" in next(
        f["detail"] for f in out[1]["findings"] if f["code"] == "doi_mismatch")


def test_identifier_does_not_override_two_conflicting_fields():
    # title and DOI of ROBIN, but another first author and journal: a hybrid, not ROBIN
    text = f"Price M, Ostrom QT. {TITLE}. Nat Cancer. 2023;4:100-110. doi:10.1093/neuonc/noaf103."
    st, out = status([text])
    assert out[1]["pmid"] == "" and st[0] != "VERIFIED"


def test_incomplete_reference_is_not_called_another_kind_of_source():
    st, out = status(["Deacon S, Cahyani I. Neuro Oncol. 2031;99(9):1-2."])
    assert st[0] == "UNRESOLVED" and "reference_incomplete" in codes(out)
    assert "lacks" in next(f["detail"] for f in out[1]["findings"] if f["code"] == "reference_incomplete")


def test_author_forms_from_real_reference_lists():
    assert author_list("Shaughnessy JD, Jr, Zhan F, et al") == ["Shaughnessy JD Jr", "Zhan F"]
    assert author_list("Kok, CH, Saunders, VA") == ["Kok CH", "Saunders VA"]
    assert split_author("Lim S-l") == ("lim", "SL")
    assert split_author("Van de Donk NWCJ") == ("van de donk", "NWCJ")
    assert first_author_surname("Lim S-l, Engelhardt M") == "lim"
    assert first_author_surname("Chung T-H, Chng WJ") == "chung"


def test_sici_doi_is_read_whole():
    r = parse_reference_text("Durie BG, Salmon SE. A clinical staging system for multiple myeloma. Cancer. "
                             "1975;36(3):842-54. doi:10.1002/1097-0142(197509)36:3<842::aid-cncr2820360303>3.0.co;2-u.")
    assert r["doi"] == "10.1002/1097-0142(197509)36:3<842::aid-cncr2820360303>3.0.co;2-u"
    r = parse_reference_text("X Y. T. Lancet Oncol. 2016;17:e328 (doi:10.1016/S1470-2045(16)30206-6).")
    assert r["doi"] == "10.1016/S1470-2045(16)30206-6"


def test_genuine_reference_is_still_verified():
    st, out = status([ROBIN.format(year=2025, vol=27, tail="")])
    assert st == ["VERIFIED"] and not out[1]["findings"]


# ---- journal volume plausibility for references PubMed could not find ----

class _VolumeClient:
    """Answers only the two volume check searches."""

    def __init__(self, journal_years=True, volume=False, fail=False):
        self.journal_years, self.volume, self.fail, self.terms = journal_years, volume, fail, []

    def esearch(self, term, retmax=10, db="pubmed"):
        from refcheck.pubmed import NCBIError
        self.terms.append(term)
        if self.fail:
            raise NCBIError("down")
        if "[vi]" in term:
            return ["1"] if self.volume else []
        return ["1"] if self.journal_years else []


def _fake(**kw):
    ref = dict(journal="Blood", volume="38", year="2024", title="x", authors="Smith J", pages="1-2")
    ref.update(kw)
    return ref


def test_volume_check_queries_journal_then_volume_in_a_three_year_window():
    from refcheck.pubmed import check_volume
    c = _VolumeClient()
    out = check_volume(c, _fake())
    assert out == dict(journal="Blood", volume="38", years="2023 to 2025", volume_found=False)
    assert c.terms == ['"Blood"[ta] AND 2023:2025[dp]', '"Blood"[ta] AND 38[vi] AND 2023:2025[dp]']
    assert check_volume(_VolumeClient(volume=True), _fake())["volume_found"] is True


def test_volume_check_abstains_when_it_cannot_say():
    from refcheck.pubmed import check_volume
    assert check_volume(_VolumeClient(journal_years=False), _fake()) is None   # unknown abbreviation
    assert check_volume(_VolumeClient(fail=True), _fake()) is None
    assert check_volume(_VolumeClient(), _fake(volume="Suppl 2")) is None
    assert check_volume(_VolumeClient(), _fake(journal="")) is None
    assert check_volume(_VolumeClient(), _fake(year="in press")) is None


def test_missing_volume_is_a_major_finding_but_not_a_fabrication_status():
    from refcheck.verify import classify_reference
    ref = _fake(num=1, text="Smith J. x. Blood. 2024;38:1-2.", doi="")
    match = dict(status="NO_MATCH", methods_run=["citmatch", "volume-check"], identifier_hits={},
                 volume_check=dict(journal="Blood", volume="38", years="2023 to 2025", volume_found=False))
    out = classify_reference(ref, match, "", "automatic", {})
    f = {x["code"]: x for x in out["findings"]}
    assert f["volume_not_in_pubmed"]["severity"] == "major"
    assert "probably wrong" in f["volume_not_in_pubmed"]["detail"]
    assert out["status"] == "NOT_FOUND_IN_PUBMED"
    match["volume_check"]["volume_found"] = True
    assert "volume_not_in_pubmed" not in {x["code"] for x in classify_reference(ref, match, "", "automatic", {})["findings"]}


def test_volume_check_runs_when_candidates_were_seen_but_rejected(monkeypatch):
    """Live run: fictional references with a common first author (Zhou) drew loose PubMed
    candidates, and the volume check was skipped. It must run for every unmatched reference."""
    import refcheck.pubmed as pm
    from fixtures import MockClient
    from refcheck.extract import parse_reference_text
    calls = []
    monkeypatch.setattr(pm, "check_volume", lambda client, ref: calls.append(ref["volume"]) or
                        dict(journal="Cancer", volume="38", years="2023 to 2025", volume_found=False))
    text = ("Zhou L, Moreau B. ROBIN: A unified nanopore-based assay integrating intraoperative methylome "
            "classification and next-day comprehensive profiling for ultra-rapid tumor diagnosis. "
            "Cancer. 2024;38:1-9.")
    res = pm.match_reference(MockClient(), parse_reference_text(text))
    assert res["status"] == "NO_MATCH" and res["candidates_seen"] >= 1
    assert calls == ["38"] and res["volume_check"]["volume_found"] is False


def test_ris_marks_pubmed_group_authors_as_corporate():
    from refcheck.export import _ris_lines
    pub = dict(authors_full=["Cancer Genome Atlas Research Network", "Ley, Timothy J"], title="T", journal="J",
               iso_journal="J", year="2013", volume="1", issue="", pages="1-2", doi="", pmid="1", pmcid="")
    lines = _ris_lines(dict(pub=pub, refs=[dict(num=1, text="x")], statuses=["VERIFIED"], parsed={}, record_number=1))
    assert "AU  - Cancer Genome Atlas Research Network," in lines and "AU  - Ley, Timothy J" in lines


def test_accented_initials_from_pubmed_are_initials():
    """Live run: PubMed writes 'Diaz Arias JÁ', 'Perez MÁ', 'Perez-Benito Á'; the accented capital
    was read as part of the surname and correct authors were reported as missing (major)."""
    from refcheck.textutil import split_author
    from refcheck.verify import compare_authors
    assert split_author("Díaz Arias JÁ") == ("diaz arias", "JA")
    assert split_author("Pérez-Benito Á") == ("perez benito", "A")
    ref = dict(authors="Pérez-Benito Á, García-Aznar JM, Gómez-Benito MJ, Pérez M")
    rec = dict(authors="Pérez-Benito Á; García-Aznar JM; Gómez-Benito MJ; Pérez MÁ")
    assert compare_authors(ref, rec, "PubMed") == []
    ref = dict(authors="Mosquera Orgueira A, González Pérez MS, Díaz Arias J, Antelo Rodríguez B, Mateos MV")
    rec = dict(authors="Mosquera Orgueira A; González Pérez MS; Díaz Arias JÁ; Antelo Rodríguez B; Mateos MV")
    assert compare_authors(ref, rec, "PubMed") == []


def test_a_comment_or_letter_can_be_matched_automatically():
    """Live run: PubMed types a correspondence letter (PMID 23411466) as Letter and Comment; RefCheck
    treated Comment as a notice and never accepted it."""
    from refcheck.config import Settings
    from refcheck.pubmed import decide
    s = Settings(cache_path="")
    best = dict(pmid="23411466", score=101, title_similarity=1.0, match_reasons="title-exact;first-author;year",
                pubtypes=["Letter", "Research Support, Non-U.S. Gov't", "Comment"])
    assert decide(best, None, s)[0] == "AUTO_MATCH"
    erratum = dict(best, pubtypes=["Published Erratum"])
    assert decide(erratum, None, s)[0] == "REVIEW"
    # a comment is still not a rival to the article it comments on
    rival = dict(pmid="2", score=99, title_similarity=0.97, match_reasons="title", pubtypes=["Comment"])
    article = dict(best, pubtypes=["Journal Article"])
    assert decide(article, rival, s)[0] == "AUTO_MATCH"


def test_authors_ending_et_al_comma_are_split_from_the_title():
    """Live benchmark case C16-023: 'Shanmuganathan, N, Yeung, DT, ..., et al, Impact of ...' gave no
    authors and a title carrying the author list, so a correct PMID was left unconfirmed."""
    from refcheck.extract import parse_reference_text
    r = parse_reference_text("Shanmuganathan, N, Yeung, DT, Wadham, Fernandes, A, Maqsood, M, Shahrin, et al, Impact "
                             "of ASXL1 at diagnosis in patients with CML receiving frontline potent TKIs: high risk of "
                             "kinase domain mutations. Blood. 2025;146(23):2821-32. PMID:40896831.")
    assert r["authors"].startswith("Shanmuganathan, N") and r["title"].startswith("Impact of ASXL1")
    r = parse_reference_text("Shanmuganathan, N., Yeung, D.T., et al., Impact of ASXL1 at diagnosis in CML. "
                             "Blood. 2025;146:2821-32.")
    assert r["authors"] == "Shanmuganathan, N., Yeung, D.T., et al" and r["title"] == "Impact of ASXL1 at diagnosis in CML"
    # titles that merely contain 'et al' stay whole
    for t in ("The et al, phenomenon in citation studies, revisited. Scientometrics. 2019;5:1-9.",
              "Gene therapy for CML, et al, a commentary on trials. Blood. 2019;5:1-9."):
        assert not parse_reference_text(t)["authors"]
    # ordinary Vancouver is unchanged
    r = parse_reference_text("Smith J, Jones K, et al. A normal Vancouver title. Blood. 2020;1:1-2.")
    assert r["authors"] == "Smith J, Jones K, et al" and r["title"] == "A normal Vancouver title"


def test_authors_left_out_or_added():
    # a gap between listed authors (Cahyani I skipped)
    st, out = status([robin(authors="Deacon S, Holmes N, Fox G, et al")])
    f = next(f for f in out[1]["findings"] if f["code"] == "authors_omitted")
    assert "Cahyani" in f["detail"] and st[0] == "METADATA_ERROR"
    # PubMed lists 6 authors, Vancouver lists them all: stopping early without 'et al.' omits authors
    cbtrus = ("Price M, Ballard CAP, Benedetti JR. CBTRUS Statistical Report: Primary Brain and Other Central Nervous "
              "System Tumors Diagnosed in the United States in 2018-2022. Neuro Oncol. 2025;27(Supplement_4):iv1-iv66.")
    st, out = status([cbtrus])
    assert "authors_omitted" in codes(out) and "3 of the 6" in next(
        f["detail"] for f in out[1]["findings"] if f["code"] == "authors_omitted")
    # the same reference with 'et al.' is fine
    st, out = status([cbtrus.replace("Benedetti JR.", "Benedetti JR, et al.")])
    assert "authors_omitted" not in codes(out)
    # all six listed: verified
    full = cbtrus.replace("Benedetti JR.", "Benedetti JR, Kruchko C, Barnholtz-Sloan JS, Ostrom QT.")
    assert status([full])[0] == ["VERIFIED"]
    # an invented author inserted in a complete list, the record shorter than the reference (since 1996)
    st, out = status([full.replace("Kruchko C,", "Kruchko C, Invented Q,")])
    assert "authors_not_in_record" in codes(out) and st[0] == "METADATA_ERROR"


@pytest.mark.parametrize("listed, record", [
    ("Farswan A, Jena L, Kaur G, Gupta A, Gupta R, Rani L, et al",
     "Farswan A; Jena L; Kaur G; Gupta A; Gupta R; Rani L; Kumar L"),
    ("Xie C, Zhong L, Luo J, Luo J, Wu Y, Zheng S, et al", "Xie C; Zhong L; Luo J; Luo J; Wu Y; Zheng S; Li X"),
    ("Wang SH, Chen NS, Hsu YT, Hsu CW, Medeiros LJ, Chang KC", "Wang SH; Chen NS; Hsu YT; Hsu CW; Medeiros LJ; Chang KC"),
])
def test_authors_sharing_a_surname_are_not_missing(listed, record):
    # found on the real references (22 November 2026): 8 false alarms before this was fixed
    from refcheck.verify import compare_authors
    assert compare_authors({"authors": listed}, {"authors": record, "year": "2023"}, "PubMed") == []


def test_a_skipped_author_with_a_shared_surname_is_still_reported():
    from refcheck.verify import compare_authors
    out = compare_authors({"authors": "Zhang Y, Wang J, et al"}, {"authors": "Zhang Y; Zhang H; Wang J", "year": "2024"},
                          "PubMed")
    assert [f["code"] for f in out] == ["authors_omitted"] and "Zhang H" in out[0]["detail"]


@pytest.mark.parametrize("pubmed, typed", [
    ("Óskarsson JÞ", "Óskarsson J"), ("Ðorđević N", "Dordevic N"), ("Øvrebø KØ", "Ovrebo K"),
    ("Łukasiewicz Ł", "Lukasiewicz L"), ("Straße A", "Strasse A"),
])
def test_special_latin_letters_in_names(pubmed, typed):
    # found on the real references (22 November 2026): 'Óskarsson JÞ' lost its initials
    from refcheck.textutil import split_author
    a, b = split_author(pubmed), split_author(typed)
    assert a[0] == b[0] and a[1] and a[1].startswith(b[1][:1])


def test_icelandic_initial_is_not_a_missing_author():
    from refcheck.verify import compare_authors
    rec = "Pérez-Escurza O; Kolijn PM; Óskarsson JÞ; González-González M; Jara-Acevedo M; Del Pozo J; Lécrevisse Q"
    ref = "Pérez-Escurza O, Kolijn PM, Óskarsson J, González-González M, Jara-Acevedo M, Del Pozo J, et al"
    assert compare_authors({"authors": ref}, {"authors": rec, "year": "2026"}, "PubMed") == []


def test_year_then_issue_without_a_volume():
    # J Vis Exp numbers issues only (found on the real references, 22 November 2026)
    r = parse_reference_text("Xie S, Liao C, Xin L, Lin C. Next-Generation Sequencing Clonality Assays: Clinical "
                             "Validation for Minimal Residual Disease Monitoring in Multiple Myeloma. J Vis Exp. 2026(234).")
    assert (r["journal"], r["year"], r["volume"], r["issue"]) == ("J Vis Exp", "2026", "", "234")
    r = parse_reference_text("Smith J, Lee K. A visual protocol for flow cytometry. J Vis Exp. 2019(146):e59001.")
    assert (r["journal"], r["issue"], r["pages"]) == ("J Vis Exp", "146", "e59001")
    r = parse_reference_text("Smith J. Another study. Blood. 2020 (abstract).")
    assert r["issue"] == ""                                       # a bracket without a digit is not an issue


def test_year_then_locator_without_a_volume():
    # PubMed's own form for articles in press and preprints (step 4 hard cases, 22 November 2026)
    cases = [
        ("Heymann WR. WITHDRAWN: Addressing Vancomycin Adverse Cutaneous Reactions. J Am Acad Dermatol. "
         "2024:S0190-9622(24)02755-5.", "J Am Acad Dermatol", "S0190-9622(24)02755-5"),
        ("Yordanova MM, Slattery C, Baranova-Gurvich M. Triple coding in human SRD5A1 mRNA. Res Sq. "
         "2024:rs.3.rs-5390104.", "Res Sq", "rs.3.rs-5390104"),
        ("Dawood M, Heavner B, Wheeler MM. GREGoR: Accelerating Genomics for Rare Diseases. ArXiv. "
         "2024:arXiv:2412.14338v1.", "ArXiv", "arXiv:2412.14338v1"),
        ("Thalalla Gamage S, Howpay Manage S. A sequence-specific RNA acetylation catalyst. bioRxiv. "
         "2024:2024.10.25.620255.", "bioRxiv", "2024.10.25.620255"),
        ("Jacobs HT, Moore AL. WITHDRAWN: There is often an alternative. Biochim Biophys Acta Bioenerg. "
         "2024:149534.", "Biochim Biophys Acta Bioenerg", "149534"),
    ]
    for text, journal, pages in cases:
        r = parse_reference_text(text)
        assert (r["journal"], r["year"], r["volume"], r["pages"]) == (journal, "2024", "", pages), text
    # 'Year;volume:pages' and 'Year:volume:pages' keep their volume
    r = parse_reference_text("Smith J, Lee K. A study of things. Blood. 2020;135:12-20.")
    assert (r["volume"], r["pages"]) == ("135", "12-20")
    r = parse_reference_text("Smith J, Lee K. A study of things. Blood. 2020:135(4):12-20.")
    assert r["volume"] == "135"
    # a title that starts with a year and a colon is not a locator
    r = parse_reference_text("Smith J. Pandemic review. 2020: A year to forget. Lancet. 2021;397:1-3.")
    assert (r["journal"], r["year"], r["volume"]) == ("Lancet", "2021", "397")


def test_nature_and_science_authors_with_particles():
    # the last author used to slip into the title (styles benchmark, 22 November 2026)
    r = parse_reference_text("Stupp, R., Mason, W. P. & van den Bent, M. J. Radiotherapy plus concomitant and "
                             "adjuvant temozolomide for glioblastoma. N Engl J Med 352, 987–96 (2005).")
    assert r["authors"].endswith("van den Bent, M. J.") and r["title"].startswith("Radiotherapy")
    r = parse_reference_text("Schwartz, L. H., Litière, S. & de Vries, E. RECIST 1.1-Update and clarification: "
                             "From the RECIST committee. Eur J Cancer 62, 132–7 (2016).")
    assert r["authors"].endswith("de Vries, E.") and r["title"].startswith("RECIST")
    r = parse_reference_text("R. Stupp, W. P. Mason, M. J. van den Bent, Radiotherapy plus concomitant and "
                             "adjuvant temozolomide for glioblastoma. N Engl J Med 352, 987–96 (2005).")
    assert r["authors"].endswith("M. J. van den Bent") and r["title"].startswith("Radiotherapy")
    r = parse_reference_text("J. Bladé, M. Beksac, J. Caers, A. Jurczyszyn, M. von Lilienfeld-Toal, P. Moreau, "
                             "Extramedullary disease in multiple myeloma: a systematic literature review. "
                             "Blood Cancer J 12, 45 (2022).")
    assert r["authors"].endswith("P. Moreau") and r["title"].startswith("Extramedullary")
    r = parse_reference_text("Pérez-Benito Á, García-Aznar, J. M., Gómez-Benito, M. J. & Pérez, M. Patient-specific "
                             "prostate tumour growth simulation: a first step towards the digital twin. "
                             "Front Physiol 15, 1421591 (2024).")
    assert r["authors"].endswith("Pérez, M.") and r["title"].startswith("Patient-specific")


def test_withdrawn_record_that_is_its_own_notice_can_match():
    # PMIDs 33387564 and 39614461: PubMed types one record as 'Retraction Notice' and
    # 'Retracted Publication' (step 4 fresh hard cases, 22 November 2026)
    from refcheck.pubmed import decide
    best = dict(pmid="33387564", score=95, match_reasons="title;journal;year;first-author",
                title_similarity=1.0, pubtypes=["Retraction Notice", "Introductory Journal Article", "Retracted Publication"])
    assert decide(best, None)[0] == "AUTO_MATCH"
    # a true notice record still never matches automatically
    for types in (["Retraction of Publication"], ["Retraction Notice"], ["Published Erratum"],
                  ["Expression of Concern"], ["Published Erratum", "Retracted Publication"]):
        assert decide(dict(best, pubtypes=types), None)[0] == "REVIEW", types


def test_list_number_without_a_space(tmp_path):
    # '1.van de Donk' and '1.Smith', as pasted from a PDF (styles benchmark, 23 November 2026)
    from refcheck.extract import extract_references
    p = tmp_path / "refs.txt"
    p.write_text("References\n"
                 "1.van de Donk NWCJ, Pawlyn C, Yong KL. Multiple myeloma. Lancet. 2021;397:410-27.\n"
                 "2.de Bono J, Mateo J, Fizazi K. Olaparib for Metastatic Castration-Resistant Prostate Cancer. "
                 "N Engl J Med. 2020;382:2091-102.\n"
                 "3.Smith J, Lee K. A study of things. Blood. 2020;135:12-20.\n", encoding="utf-8")
    refs = extract_references(str(p))["refs"]
    assert [r["num"] for r in refs] == [1, 2, 3]
    assert [r["authors"].split(",")[0] for r in refs] == ["van de Donk NWCJ", "de Bono J", "Smith J"]
    # a wrapped line starting with a dose or an edition is not a new reference
    p.write_text("References\n"
                 "1. Smith J, Lee K. Dosing of drug X at\n"
                 "2.5 mg per day. Blood. 2020;135:12-20.\n"
                 "2. Jones A. A textbook of things.\n"
                 "3rd ed. London: Publisher; 2019.\n", encoding="utf-8")
    refs = extract_references(str(p))["refs"]
    assert len(refs) == 2 and "2.5 mg" in refs[0]["text"] and "3rd ed" in refs[1]["text"]


def test_apa_subtitle_harvard_comma_journal_ama_online():
    # styles benchmark, 23 November 2026
    r = parse_reference_text("Mittelman, M., Israel, A., & Kepten, E. (2025). Can we identify individuals at risk to "
                             "develop multiple myeloma? A machine learning-based predictive model. British Journal of "
                             "Haematology, 207(2), 387–94.")
    assert r["title"].endswith("predictive model") and r["journal"] == "British Journal of Haematology"
    r = parse_reference_text("Smith, J. (2020). Effects of drug A vs. drug B in myeloma. New England Journal "
                             "of Medicine, 382(4), 12–20.")
    assert r["title"] == "Effects of drug A vs. drug B in myeloma" and r["volume"] == "382"
    # a journal name with full stops keeps them ('Hematology. American Society of Hematology. ...')
    r = parse_reference_text("Gay, F., Bertuglia, G., & Mina, R. (2023). A rational approach to functional high-risk "
                             "myeloma. Hematology. American Society of Hematology. Education Program, 2023(1), 433–42.")
    assert r["title"] == "A rational approach to functional high-risk myeloma"
    assert r["journal"] == "Hematology. American Society of Hematology. Education Program"
    r = parse_reference_text("van Beers, E.H. et al. (2017) “Prognostic Validation of SKY92 and Its Combination With "
                             "ISS”, Clinical Lymphoma, Myeloma & Leukemia, 17(9), pp. 555–62.")
    assert (r["journal"], r["volume"], r["pages"]) == ("Clinical Lymphoma, Myeloma & Leukemia", "17", "555-62")
    r = parse_reference_text("Hughes TP, Yong AS, Ross DM. The Evolution of Treatment-Free Remission. Blood. "
                             "Published online 2025.")
    assert (r["title"], r["journal"], r["year"]) == ("The Evolution of Treatment-Free Remission", "Blood", "2025")
    r = parse_reference_text("Smith J, Lee K. A study of things. JAMA. Published online March 3, 2025. "
                             "doi:10.1001/jama.2025.1234")
    assert (r["journal"], r["year"], r["doi"]) == ("JAMA", "2025", "10.1001/jama.2025.1234")


def test_double_surname_indexed_under_its_last_part():
    # PMID 33707652: the reference has 'Gómez Casares MT', PubMed 'Casares MTG' (Gemini set, 23 November 2026)
    from refcheck.verify import compare_authors
    rec = {"year": "2021", "authors": "Radich JP; Hochhaus A; Masszi T; Hellmann A; Stentoft J; Casares MTG; "
                                      "García-Gutiérrez JV; Conneally E"}
    ref = {"authors": "Radich JP, Hochhaus A, Masszi T, Hellmann A, Stentoft J, Gómez Casares MT, et al", "text": ""}
    assert [f["code"] for f in compare_authors(ref, rec, "PubMed")] == []
    # a different double surname is still not in the record
    ref2 = dict(ref, authors=ref["authors"].replace("Gómez Casares MT", "Pérez Casares MT"))
    assert [f["code"] for f in compare_authors(ref2, rec, "PubMed")] == ["authors_not_in_record"]   # folds to MTP


def test_review_table_authors_and_online_year():
    from refcheck.review import compare_fields
    ref = dict(title="A panel", authors="Cross NCP, White HE, Ernst T, Welker L, Ferrier C, et al", journal="Leukemia",
               year="2024", volume="30", issue="9", pages="1844-52", doi="")
    cand = dict(title="A panel", first_author="Cross", authors="Cross NC; White HE; Ernst T; Welden L; Dietz C",
                iso_journal="Leukemia", journal="Leukemia", year="2025", epub_year="2024", volume="30", issue="9",
                pages="1844-52", doi="")
    rows = {r[0]: r for r in compare_fields(ref, cand)}
    assert rows["Authors"][3] == "❌ not in PubMed: Welker L, Ferrier C"
    assert rows["Year"][3] == "✅ online year (print 2025)" and rows["Year"][2] == "2025 (online 2024)"
    rows = {r[0]: r for r in compare_fields(dict(ref, year="2023"), cand)}
    assert rows["Year"][3] == "❌"
    rows = {r[0]: r for r in compare_fields(dict(ref, authors="Cross NC, White HE, Ernst T"), cand)}
    assert rows["Authors"][3] == "✅"


def test_report_word_in_a_journal_title_is_not_a_report():
    # Gemini myeloma #64 (23 November 2026): a journal article whose title says 'a report from the IFM'
    from refcheck.verify import source_type
    t = ("Hebraud B, Leleu X, Lauwers-Cances V. Deletion of the long arm of chromosome 1 (1q21) is a strong negative "
         "prognostic factor in multiple myeloma: a report from the IFM. Leukemia. 2014;28(9):1910-2.")
    assert source_type(dict(parse_reference_text(t), text=t))[0] == "journal"
    # real reports, books and web pages are still recognised
    for t, kind in (("World Health Organization. Global report on cancer. Geneva: WHO; 2020.", "non_pubmed"),
                    ("Swerdlow SH, Campo E, Harris NL, editors. WHO classification of tumours of haematopoietic and "
                     "lymphoid tissues. 4th ed. Lyon: IARC Press; 2017.", "non_pubmed"),
                    ("Smith J. Myeloma statistics. Available from: https://example.org/myeloma. Accessed 1 May 2024.",
                     "non_pubmed")):
        assert source_type(dict(parse_reference_text(t), text=t))[0] == kind, t


def test_export_record_types_for_references_not_in_pubmed(tmp_path):
    # step 6 mock manuscript (23 November 2026): a web page was exported as a journal article
    from refcheck.export import write_ris, write_endnote_xml
    cases = [
        ("National Comprehensive Cancer Network. NCCN guidelines for patients: chronic myeloid leukemia. 2024. "
         "Available from: https://www.nccn.org/patientresources. Accessed 1 November 2026.", "ELEC", "Web Page",
         ["AU  - National Comprehensive Cancer Network,", "TI  - NCCN guidelines for patients: chronic myeloid leukemia",
          "UR  - https://www.nccn.org/patientresources", "Y2  - 1 November 2026"]),
        ("WHO Classification of Tumours Editorial Board. Haematolymphoid tumours. 5th ed. Lyon: International Agency "
         "for Research on Cancer; 2024.", "BOOK", "Book",
         ["ET  - 5th", "CY  - Lyon", "PB  - International Agency for Research on Cancer"]),
        ("Hughes TP, Ross DM. Chronic myeloid leukaemia. In: Hoffbrand AV, editor. Postgraduate haematology. 8th ed. "
         "Oxford: Wiley; 2022. p. 300-20.", "CHAP", "Book Section", ["T2  - Postgraduate haematology", "PB  - Wiley"]),
        ("Mahon FX, Rea D. Discontinuation of imatinib. Lancet Oncol. 2010;11(11):1029-35.", "JOUR", "Journal Article",
         ["T2  - Lancet Oncol"]),
    ]
    for k, (text, ris_type, xml_type, lines) in enumerate(cases, 1):
        ref = dict(parse_reference_text(text), text=text, num=k)
        rec = dict(record_number=k, refs=[ref], parsed=ref, pub=None, statuses=["NO_MATCH"])
        write_ris([rec], tmp_path / "r.ris")
        ris = (tmp_path / "r.ris").read_text(encoding="utf-8")
        assert f"TY  - {ris_type}" in ris, text
        for line in lines:
            assert line in ris, (line, ris)
        write_endnote_xml([rec], tmp_path / "r.xml")
        assert f'<ref-type name="{xml_type}">' in (tmp_path / "r.xml").read_text(encoding="utf-8")


def test_pdf_line_break_after_a_hyphen_joins_without_a_space():
    # 303 reference PDF (23 November 2026): 'Next-' / 'Generation' was read 'Next- Generation'
    from refcheck.extract import find_reference_list
    lines = [(t, False, None) for t in (
        "References",
        "1. Moreno C, Reina-", "Ortiz C, et al. Concordance of Next-", "Generation Sequencing. Int J Mol Sci.",
        "2022;29(1):610-", "4.",
        "2. Smith J. A gene-", "ration study. Blood. 2020;135:12-20.")]
    items, _, _ = find_reference_list(lines)
    assert items[0]["text"] == ("Moreno C, Reina-Ortiz C, et al. Concordance of Next-Generation Sequencing. "
                                "Int J Mol Sci. 2022;29(1):610-4.")
    assert items[1]["text"] == "Smith J. A gene-ration study. Blood. 2020;135:12-20."
    # a line that ends a sentence still gets its space
    items, _, _ = find_reference_list([(t, False, None) for t in ("References", "1. Smith J. A study.", "Blood. 2020;135:12-20.")])
    assert items[0]["text"] == "Smith J. A study. Blood. 2020;135:12-20."
