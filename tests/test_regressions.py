"""Regression tests for problems found by stress testing RefCheck with mock and real manuscripts.
They need only the package and tests/data."""
import sys
from pathlib import Path

import pytest
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data"
sys.path.insert(0, str(ROOT))

from refcheck.annotate import annotate_docx, front_matter_end  # noqa: E402
from refcheck.extract import drop_number_column, drop_page_margins, extract_references  # noqa: E402


def _doc(paras, path):
    d = Document()
    for runs in paras:
        p = d.add_paragraph()
        for text, sup in runs:
            p.add_run(text).font.superscript = sup
    d.save(str(path))
    return path


def _texts(path):
    return [p.text for p in Document(str(path)).paragraphs]


def test_superscript_citation_at_the_end_of_a_paragraph_is_converted(tmp_path):
    """It was taken for an exponent: the text after it is empty, and '' is 'in' any string."""
    src = _doc([[("Introduction", False)], [("Shown before.", False), ("1", True)],
                [("Cohort", False), ("a", True), (" ", False), ("1,2", True)]], tmp_path / "a.docx")
    log, _ = annotate_docx(src, tmp_path / "b.docx", {1: 1, 2: 2}, {1: ("A", "2000"), 2: ("B", "2001")})
    t = _texts(tmp_path / "b.docx")
    assert t[1] == "Shown before.{A, 2000 #1}" and t[2] == "Cohorta {A, 2000 #1; B, 2001 #2}"
    assert not [e for e in log if e["problem"]]


def test_superscript_exponents_still_left_alone(tmp_path):
    src = _doc([[("Introduction", False)], [("above 1.5 × 10", False), ("9", True), ("/L and 260 mg/m", False),
                                             ("2", True), (" given", False)]], tmp_path / "a.docx")
    log, _ = annotate_docx(src, tmp_path / "b.docx", {i: i for i in range(1, 10)},
                           {i: ("A", "2000") for i in range(1, 10)})
    assert _texts(tmp_path / "b.docx")[1] == "above 1.5 × 109/L and 260 mg/m2 given"
    assert all("exponent" in e["problem"] for e in log) and len(log) == 2


def test_title_page_superscripts_are_affiliation_marks(tmp_path):
    src = _doc([[("A mock title", False)], [("Ana Silva", False), ("1,2", True), (", Ben Okafor", False), ("1", True)],
                [("1", True), ("Department of Haematology", False)], [("Abstract", False)],
                [("Shown before", False), ("2", True), (".", False)]], tmp_path / "a.docx")
    log, warns = annotate_docx(src, tmp_path / "b.docx", {1: 1, 2: 2}, {1: ("A", "2000"), 2: ("B", "2001")})
    t = _texts(tmp_path / "b.docx")
    assert t[1] == "Ana Silva1,2, Ben Okafor1" and t[2] == "1Department of Haematology"
    assert t[4] == "Shown before{B, 2001 #2}."
    assert any("affiliation marks" in w for w in warns)


def test_no_front_matter_without_an_abstract_or_introduction_heading(tmp_path):
    """A document with no such heading is all body: its superscripts are converted as before."""
    src = _doc([[("Shown before", False), ("1", True)], [("And again", False), ("2", True)]], tmp_path / "a.docx")
    annotate_docx(src, tmp_path / "b.docx", {1: 1, 2: 2}, {1: ("A", "2000"), 2: ("B", "2001")})
    assert _texts(tmp_path / "b.docx") == ["Shown before{A, 2000 #1}", "And again{B, 2001 #2}"]
    assert front_matter_end([p._p for p in Document(str(src)).paragraphs]) == 0


def test_lone_year_in_parentheses_is_not_a_missing_citation(tmp_path):
    src = _doc([[("Samples from 2019 (2019) were tested (1) [2019].", False)]], tmp_path / "a.docx")
    log, _ = annotate_docx(src, tmp_path / "b.docx", {1: 1}, {1: ("A", "2000")}, parentheses=True)
    assert _texts(tmp_path / "b.docx")[0] == "Samples from 2019 (2019) were tested {A, 2000 #1} [2019]."
    assert [e["original"] for e in log] == ["(1)"]


def test_line_number_block_of_a_pdf_page_is_dropped():
    page = ["10. Korde N, et al. JAMA Oncol (2015) 1(6):746-54. doi:", "10.1001/jamaoncol.2015.2010", "Page 4"] + \
        [str(n) for n in range(47, 67)]
    assert drop_number_column(page) == page[:3]
    short = ["Table", "1", "2", "3", "next"]          # too short a run to be line numbers
    assert drop_number_column(short) == short


def test_running_head_of_a_two_page_pdf_is_dropped():
    head = "Mock Journal of Haematology | Article | Test material"
    pages = [[head, "Title", "17. Irani YD, et al. Br J Haematol. 191, 433-441 (2020).", "Page 1"],
             [head, "18. Munshi NC, et al. JAMA Oncol. 3, 28-35 (2017).", "Page 2"]]
    assert drop_page_margins(pages) == [["Title", "17. Irani YD, et al. Br J Haematol. 191, 433-441 (2020)."],
                                         ["18. Munshi NC, et al. JAMA Oncol. 3, 28-35 (2017)."]]
    one = [["Only page", "1. Smith J. Blood. 2020;1:1-2."]]
    assert drop_page_margins(one) == one


def test_pdf_with_line_number_block_keeps_doi_digits():
    refs = extract_references(DATA / "stress_submission_mock.pdf")["refs"]
    assert refs[9]["doi"] == "10.1001/jamaoncol.2015.2010"
    head = "Mock Journal of Haematology"
    assert not [r["num"] for r in extract_references(DATA / "stress_journal_mock.pdf")["refs"] if head in r["text"]]


def _annot(paras, tmp_path, n=30, **opts):
    src = _doc(paras, tmp_path / "a.docx")
    log, warns = annotate_docx(src, tmp_path / "b.docx", {i: i for i in range(1, n + 1)},
                               {i: (f"A{i}", "2000") for i in range(1, n + 1)}, **opts)
    return _texts(tmp_path / "b.docx"), log, warns


def test_ieee_range_cites_every_number(tmp_path):
    t, _, _ = _annot([[("as in [4]\u2013[6] and [7]-[8].", False)]], tmp_path)
    assert t[0] == "as in {A4, 2000 #4; A5, 2000 #5; A6, 2000 #6} and {A7, 2000 #7; A8, 2000 #8}."


def test_superscript_after_a_one_letter_symbol_is_a_power(tmp_path):
    t, log, _ = _annot([[("fit (R", False), ("2", True), (" = 0.81; I", False), ("2", True),
                         (" = 45%) by \u03c7", False), ("2", True), (" test; shown", False), ("3", True)]], tmp_path)
    assert t[0] == "fit (R2 = 0.81; I2 = 45%) by \u03c72 test; shown{A3, 2000 #3}"


def test_glued_citation_after_short_word_and_flag_words(tmp_path):
    t, log, _ = _annot([[("in long follow up7 at week24 with IDH1 and H3K27me3 in 1p19q.", False)]], tmp_path, glued=True)
    assert t[0] == "in long follow up{A7, 2000 #7} at week24 with IDH1 and H3K27me3 in 1p19q."
    assert [e["original"] for e in log if e["problem"]] == ["24"]


def test_reference_list_in_a_word_table(tmp_path):
    from docx import Document
    d = Document()
    d.add_paragraph("Introduction")
    d.add_paragraph("Shown before [1,2].")
    d.add_paragraph("References")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "1.", "Smith J, Lee K. A title of a paper. Blood. 2020;135(1):1-10."
    t.cell(1, 0).text, t.cell(1, 1).text = "2.", "Brown A. Another title here. Leukemia. 2019;33(2):20-30."
    d.add_paragraph("Table 1. Results")
    body = d.add_table(rows=1, cols=1)
    body.cell(0, 0).text = "kept"
    d.save(str(tmp_path / "t.docx"))
    ex = extract_references(tmp_path / "t.docx")
    assert [r["num"] for r in ex["refs"]] == [1, 2] and ex["refs"][1]["journal"] == "Leukemia"
    stats = {}
    annotate_docx(tmp_path / "t.docx", tmp_path / "o.docx", {1: 1, 2: 2}, {1: ("Smith", "2020"), 2: ("Brown", "2019")},
                  heading_para=ex["heading_para"], reflist_end_para=max(r["para_end"] for r in ex["refs"]), stats=stats)
    out = Document(str(tmp_path / "o.docx"))
    assert stats["removed_tables"] == 1 and len(out.tables) == 1 and out.tables[0].cell(0, 0).text == "kept"
    assert out.paragraphs[1].text == "Shown before {Smith, 2020 #1; Brown, 2019 #2}."


def test_tab_numbered_reference_list(tmp_path):
    src = _doc([[("Introduction", False)], [("References", False)],
                [("1\tSmith J, Lee K. A title of a paper. Blood. 2020;135(1):1-10.", False)],
                [("2\tvan Beers EH, Kuiper R. Another title. Leukemia. 2019;33(2):20-30.", False)]], tmp_path / "a.docx")
    refs = extract_references(src)["refs"]
    assert [(r["num"], r["authors"]) for r in refs] == [(1, "Smith J, Lee K"), (2, "van Beers EH, Kuiper R")]


def test_unnumbered_author_year_list_from_printed_lines(tmp_path):
    from refcheck.extract import find_reference_list
    lines = [(s, False, None) for s in [
        "References",
        "Gay, F., Bertuglia, G., & Mina, R. (2023). A rational approach to functional high-risk myeloma.",
        "Hematology, 2023(1), 433\u201342. https://doi.org/10.1182/hematology.2023000443",
        "Laubenbacher, R., Mehrad, B., & Trayanova, N. (2024). Digital twins in medicine. Nature",
        "Computational Science, 4(3), 184\u201391. https://doi.org/10.1038/s43588-024-00607-",
        "6",
        "van de Donk, N. W. C. J., & Pawlyn, C. (2021). Multiple myeloma. Lancet, 397, 410\u201327."]]
    items, _, warns = find_reference_list(lines)
    assert len(items) == 3 and items[1]["text"].endswith("s43588-024-00607-6")
    assert items[2]["text"].startswith("van de Donk") and "line that opens with an author" in warns[0]


def test_apa_journal_name_with_commas():
    from refcheck.extract import parse_reference_text
    r = parse_reference_text("Li, H., & Cao, X. (2009). A title here. Journal of immunology (Baltimore, Md. : 1950), "
                             "182(1), 240\u20139.")
    assert (r["journal"], r["volume"], r["issue"], r["pages"]) == ("Journal of immunology (Baltimore, Md.: 1950)",
                                                                   "182", "1", "240-9")


def test_misspelt_first_author_is_close_not_different():
    from refcheck.pubmed import _one_edit, first_author_any_style
    assert _one_edit("slmaon", "slamon") and _one_edit("smith", "smyth") and _one_edit("kok", "kook")
    assert not _one_edit("smith", "jones") and not _one_edit("lee", "lim")
    assert first_author_any_style({"authors": "Slmaon DJ, Shak S"}, {"first_author": "Slamon"}) == "prefix5"
    assert first_author_any_style({"authors": "Lee DJ"}, {"first_author": "Lim"}) == "differs"


def test_pdf_footnote_is_not_read_into_a_reference():
    from refcheck.extract import footnote_lines
    frags = [(700 - 14 * i, f"line {i} of a reference text") for i in range(30)] + \
            [(75, "1"), (75, " See the original method [21]."), (40, "Page 1")]
    assert footnote_lines(frags, 792) == {"1 See the original method [21]."}
    no_gap = [(700 - 14 * i, f"line {i}") for i in range(40)]
    assert footnote_lines(no_gap, 792) == set()


def test_page_margins_keep_reference_tails_that_differ_only_in_digits():
    pages = [["Head of page", "1. Davies H. Mutations. Nature.", "2002;417(6892):949-54."],
             ["2013;369(1):32-42.", "2. Byrd JC. Targeting BTK. N Engl J Med.", "2013;369(18):1691-703."],
             ["2014;370(2):10-20.", "3. Smith J. A title. Blood."]]
    kept = drop_page_margins(pages)
    assert "2002;417(6892):949-54." in kept[0] and "2013;369(1):32-42." in kept[1]


def test_invisible_characters_and_ligatures_are_cleaned():
    from refcheck.extract import clean_invisible, parse_reference_text
    assert clean_invisible("leu\u00adkaemia Sm\u200bith \ufb01rst") == "leukaemia Smith first"
    r = parse_reference_text("Sm\u200bith J, Lee K. Imatinib dis\u00adcontinuation \ufb01rst. Blood. 2020;135(1):1-10.")
    assert r["authors"] == "Smith J, Lee K" and r["title"] == "Imatinib discontinuation first"


def test_nlm_notes_after_the_citation_are_not_parsed_as_it():
    from refcheck.extract import parse_reference_text
    r = parse_reference_text("Smith J, Lee K. A title of a paper. Blood. 2020;135(1):1-10. PMCID: PMC1234567. "
                             "Erratum in: Blood. 2021;137(3):999.")
    assert (r["year"], r["volume"], r["pages"]) == ("2020", "135", "1-10")
    r = parse_reference_text("Smith J. A title. Blood [Internet]. 2020 [cited 2024 Jan 5];135(1):1-10. "
                             "Available from: https://doi.org/10.1182/blood.1")
    assert (r["journal"], r["year"], r["volume"], r["pages"]) == ("Blood", "2020", "135", "1-10")


@pytest.mark.parametrize("heading", ["REFERENCES AND NOTES", "Notes and references", "References cited"])
def test_reference_heading_variants(tmp_path, heading):
    src = _doc([[("Introduction", False)], [("Shown [1].", False)], [(heading, False)],
                [("1. Smith J, Lee K. A title of a paper. Blood. 2020;135(1):1-10.", False)]], tmp_path / "a.docx")
    ex = extract_references(src)
    assert len(ex["refs"]) == 1 and ex["heading_para"] == 2 and not ex["warnings"]


def test_greek_letter_put_back_into_its_hole():
    from refcheck.extract import _greek_back
    assert _greek_back("of pegylated interferon- 2b and dasatinib in newly diagnosed \u03b1") == \
        "of pegylated interferon-\u03b12b and dasatinib in newly diagnosed"
    two = "pre- and post- 2b \u03b1"                  # two holes: unclear, left alone
    assert _greek_back(two) == two


def test_page_furniture_needs_the_same_height_on_many_pages():
    from refcheck.extract import page_furniture
    wm = [{("In review", 300), (f"text {i}", 500)} for i in range(5)]
    assert page_furniture(wm) == {"In review"}
    wrapped = [{("discontinuation in chronic myeloid", 100 + 37 * i)} for i in range(5)]
    assert page_furniture(wrapped) == set()


def test_reference_text_is_kept_as_written():
    from refcheck.extract import parse_reference_text
    s = "Smith J, Lee K. A title. Blood. Published online Apr 5, 2025. doi:10.1182/blood.2024"
    r = parse_reference_text(s)
    assert r["text"] == s and r["year"] == "2025" and r["journal"] == "Blood"


def test_thesis_with_a_list_per_chapter(tmp_path):
    paras = []
    for c in (1, 2):
        paras += [[(f"Chapter {c}", False)], [(f"Text of chapter {c} [1,2].", False)], [("References", False)]]
        paras += [[(f"{i}. Author{c}{i} J. Title {c} {i} here. Blood. 2020;1:{i}-{i + 1}.", False)] for i in (1, 2, 3)]
    src = _doc(paras, tmp_path / "a.docx")
    ex = extract_references(src)
    assert [r["authors"] for r in ex["refs"]] == ["Author21 J", "Author22 J", "Author23 J"]
    assert ex["body_start_para"] == 6 and any("2 reference lists" in w for w in ex["warnings"])
    log, _ = annotate_docx(src, tmp_path / "b.docx", {1: 1, 2: 2, 3: 3}, {i: ("A", "2000") for i in (1, 2, 3)},
                           heading_para=ex["heading_para"], reflist_end_para=9, body_start_para=ex["body_start_para"])
    t = _texts(tmp_path / "b.docx")
    assert t[1] == "Text of chapter 1 [1,2]." and t[7] == "Text of chapter 2 {A, 2000 #1; A, 2000 #2}."


def test_broken_files_stop_with_a_clear_message(tmp_path):
    from pypdf import PdfReader, PdfWriter
    from PIL import Image
    w = PdfWriter()
    for pg in PdfReader(str(DATA / "stress_styled_mock.pdf")).pages:
        w.add_page(pg)
    w.encrypt("secret")
    with open(tmp_path / "locked.pdf", "wb") as fh:
        w.write(fh)
    Image.new("RGB", (600, 800), "white").save(tmp_path / "scan.pdf")
    d = (DATA / "mock_manuscript_endnote_updated.docx").read_bytes()
    (tmp_path / "cut.docx").write_bytes(d[: len(d) // 2])
    for f, words in (("locked.pdf", "password"), ("scan.pdf", "scanned"), ("cut.docx", "not a readable Word")):
        with pytest.raises(ValueError, match=words):
            extract_references(tmp_path / f)


def test_numbering_faults():
    ex = extract_references(DATA / "stress_numbering_mock.pdf")
    assert [r["num"] for r in ex["refs"]] == [1, 2, 3, 4, 5, 6] + list(range(8, 21))
    w = " ".join(ex["warnings"])
    assert "Number 12 is used twice" in w and "15, 16 were typed in one paragraph" in w and "from 6 to 8" in w


def test_references_without_titles_and_identifiers_alone():
    from refcheck.extract import parse_reference_text
    r = parse_reference_text("Pavlovsky C, et al. J Hematol Oncol. 2023;16:43.")
    assert (r["authors"], r["title"], r["journal"], r["volume"], r["pages"]) == \
        ("Pavlovsky C, et al", "", "J Hematol Oncol", "16", "43")
    r = parse_reference_text("Smith J, et al. doi:10.1182/blood.2020.1.")
    assert (r["authors"], r["title"], r["year"], r["doi"]) == ("Smith J, et al", "", "", "10.1182/blood.2020.1")
    r = parse_reference_text("PMID: 40225790.")
    assert (r["title"], r["pmid_hint"]) == ("", "40225790")


def test_untitled_reference_needs_every_field_to_agree():
    from refcheck.pubmed import score_candidate
    cand = dict(pmid="1", title="A long title of the paper", journal="Blood", iso_journal="Blood", year="2020",
                epub_year="", volume="135", pages="1-10", doi="10.1/x", authors="Smith J; Lee K", first_author="Smith")
    ref = dict(authors="Smith J, et al", title="", journal="Blood", year="2020", volume="135", pages="1-10",
               doi="", pmid_hint="", text="Smith J, et al. Blood. 2020;135:1-10.")
    assert score_candidate(ref, cand)[0] >= 86
    assert score_candidate(dict(ref, pages="2-10"), cand)[0] < 86
    assert score_candidate(dict(ref, authors="Jones J, et al", text="Jones J, et al. Blood. 2020;135:1-10."),
                           cand)[0] < 86


def test_errors_people_make_are_caught():
    from refcheck.pubmed import first_author_any_style, title_similarity_any_style
    from refcheck.verify import _last_page
    cand = dict(title="Natural killer cell counts are associated with molecular relapse free survival",
                first_author="Rea", authors="Rea D; Henry G; Khaznadar Z")
    assert first_author_any_style({"authors": "Henry G, Khaznadar Z"}, cand) == "prefix5"     # first author left out
    assert title_similarity_any_style({"title": "Natural killer cell counts are associated"}, cand) >= 0.88
    assert _last_page("1029-35") == "1035" and _last_page("e528-35") == "e535"[1:] and _last_page("400-416.e11") == ""


def test_collective_first_author():
    from refcheck.pubmed import _surname, first_author_any_style
    assert _surname("Cancer Genome Atlas Research Network") == "Cancer Genome Atlas Research Network"
    assert _surname("Mahon FX") == "Mahon" and _surname("Lim S-l") == "Lim"
    assert first_author_any_style({"authors": "The Cancer Genome Atlas Research Network"},
                                  {"first_author": "Cancer Genome Atlas Research Network"}) == "exact"


def test_superscript_with_brackets_or_punctuation_inside(tmp_path):
    t, log, _ = _annot([[("as shown", False), ("[1]", True), (" and", False), ("2,", True), (" then", False),
                         ("(3,4)", True), (" and last", False), ("5.", True)]], tmp_path)
    assert t[0] == "as shown{A1, 2000 #1} and{A2, 2000 #2}, then{A3, 2000 #3; A4, 2000 #4} and last{A5, 2000 #5}."


def test_bulleted_reference_list(tmp_path):
    from docx import Document
    d = Document()
    d.add_paragraph("Introduction")
    d.add_paragraph("References")
    d.add_paragraph("Smith J, Lee K. A title of a paper. Blood. 2020;135(1):1-10.", style="List Bullet")
    d.add_paragraph("Brown A. Another title here. Leukemia. 2019;33(2):20-30.", style="List Bullet")
    d.save(str(tmp_path / "b.docx"))
    assert [r["journal"] for r in extract_references(tmp_path / "b.docx")["refs"]] == ["Blood", "Leukemia"]
    from refcheck.extract import find_reference_list
    lines = [(s, False, None) for s in ["References", "\u2022 Smith J, Lee K. A title of a paper. Blood. 2020;1:1-2.",
                                        "\u2022 Brown A. Another title here. Leukemia. 2019;33:20-30."]]
    assert len(find_reference_list(lines)[0]) == 2


def test_markdown_reference_heading(tmp_path):
    (tmp_path / "a.md").write_text("# Paper\n\nText [1].\n\n## References\n\n1. Smith J, Lee K. A title of a "
                                   "paper. Blood. 2020;135(1):1-10.\n", encoding="utf-8")
    ex = extract_references(tmp_path / "a.md")
    assert len(ex["refs"]) == 1 and not ex["warnings"]


def test_line_number_after_a_word_broken_at_the_line_end():
    """Word prints line numbers glued to the line end. After 'State-' or a page range broken at its
    dash the number was kept ('State-536 of-the-Art', 'p. 163-614 70.'), found in a real manuscript."""
    from refcheck.extract import strip_line_numbers
    lines = [f"Line {i} of body text here{530 + i}" for i in range(12)]
    lines += ["Multiple Myeloma: State-542", "of-the-Art. J Clin Med. 12. Switzerland(2023).543",
              "Experience in 127 Patients. Bone Marrow Transplant. 31. England(2003). p. 163–544", "70.545",
              "pages 610-4", "Lancet Digit Health (2021) 3(3):e195–e203. doi: 10.1016/S2589-547", "7500(20)30292-2.548"]
    out = strip_line_numbers(lines)
    assert out[12:] == ["Multiple Myeloma: State-", "of-the-Art. J Clin Med. 12. Switzerland(2023).",
                        "Experience in 127 Patients. Bone Marrow Transplant. 31. England(2003). p. 163–", "70.",
                        "pages 610-4", "Lancet Digit Health (2021) 3(3):e195–e203. doi: 10.1016/S2589-",
                        "7500(20)30292-2."]


def test_endnote_frontiers_list_is_one_style():
    """A real 93 reference list from one EndNote style: 'Journal (Year) Vol(Iss):Pages' and, for
    records imported from PubMed, 'Journal. Vol. Country(Year). p. Pages'. It was reported as four
    mixed styles (Vancouver, Cell or Frontiers, APA, Nature)."""
    from refcheck.styles import manuscript_style
    refs = [dict(num=1, text="Kazandjian D. Multiple Myeloma Epidemiology and Survival: A Unique Malignancy. Semin "
                             "Oncol (2016) 43(6):676–81. Epub 20161110. doi: 10.1053/j.seminoncol.2016.11.004."),
            dict(num=2, text="Hou Q, Li X, Ma H, Fu D, Liao A. A Systematic Epidemiological Trends Analysis Study in "
                             "Global Burden of Multiple Myeloma. Sci Rep. 15. England(2025). p. 2204."),
            dict(num=3, text="Sun S, Huang Z, Tang Y. Establishment of a Multimodal Prognostic Prediction Model for "
                             "Multiple Myeloma. Turk J Haematol (2026). Epub 20260716. doi: 10.4274/tjh.2026.33678."),
            dict(num=4, text="Lee N, Moon SY, Lee JH, et al. Discrepancies between the Percentage of Plasma Cells in "
                             "Bone Marrow. Blood Cancer J. 7. United States(2017). p. e530."),
            dict(num=5, text="Romero M, Mosquera Orgueira A. How Artificial Intelligence Revolutionizes the World of "
                             "Multiple Myeloma. Frontiers in Hematology (2024) Volume 3 - 2024. doi: 10.3389/frhem.2024.1331109.")]
    s = manuscript_style(refs * 3)
    assert not s["mixed"] and list(s["counts"]) == ["Vancouver family (NLM, AMA, BMJ, PLOS, NEJM, Lancet, Bioinformatics)"]


def test_group_name_in_pubmed_author_list_is_not_an_omitted_author():
    """PubMed 33256715 lists five people and 'Precise4Q consortium'; a reference naming the five
    people was reported as leaving out an author."""
    from refcheck.verify import compare_authors
    rec = dict(authors="Amann J; Blasimme A; Vayena E; Frey D; Madai VI; Precise4Q consortium", year="2020")
    assert compare_authors(dict(authors="Amann J, Blasimme A, Vayena E, Frey D, Madai VI", text=""), rec, "x") == []
    rec = dict(authors="Amann J; Blasimme A; Vayena E; Frey D; Madai VI; Smith K", year="2020")
    assert [f["code"] for f in compare_authors(dict(authors="Amann J, Blasimme A, Vayena E, Frey D, Madai VI",
                                                     text=""), rec, "x")] == ["authors_omitted"]


def test_cell_line_names_are_not_glued_citations(tmp_path):
    t, log, _ = _annot([[("lines (697, Molm13, GM12878) as shown previously12.", False)]], tmp_path, glued=True)
    assert t[0] == "lines (697, Molm13, GM12878) as shown previously{A12, 2000 #12}."
    assert [e["original"] for e in log if e["problem"]] == ["13"]
