"""Reading references in any citation style (APA, Nature, Science, Cell, Harvard, Chicago,
IEEE, MLA, older PLOS, Vancouver) and the style independent matching evidence."""
import pytest

from refcheck.extract import parse_reference_text
from refcheck.pubmed import first_author_any_style, text_terms, title_similarity_any_style
from refcheck.textutil import author_list, split_author, surname_at_start, title_in_text

TITLE = "genomic and epigenomic landscapes of adult de novo acute myeloid leukemia"

STYLES = {
    "vancouver": "Ley TJ, Miller C, Ding L, et al. Genomic and epigenomic landscapes of adult de novo acute "
                 "myeloid leukemia. N Engl J Med. 2013;368(22):2059-2074.",
    "nature": "Ley, T. J. et al. Genomic and epigenomic landscapes of adult de novo acute myeloid leukemia. "
              "N. Engl. J. Med. 368, 2059–2074 (2013).",
    "science": "T. J. Ley et al., Genomic and epigenomic landscapes of adult de novo acute myeloid leukemia. "
               "N. Engl. J. Med. 368, 2059–2074 (2013).",
    "harvard": "Ley, T.J., Miller, C. and Ding, L. (2013) 'Genomic and epigenomic landscapes of adult de novo "
               "acute myeloid leukemia', New England Journal of Medicine, 368(22), pp. 2059–2074.",
    "chicago": "Ley, Timothy J., Christopher Miller, and Li Ding. 2013. “Genomic and Epigenomic Landscapes of "
               "Adult De Novo Acute Myeloid Leukemia.” New England Journal of Medicine 368 (22): 2059–74.",
    "ieee": "T. J. Ley, C. Miller, and L. Ding, “Genomic and epigenomic landscapes of adult de novo acute "
            "myeloid leukemia,” N. Engl. J. Med., vol. 368, no. 22, pp. 2059–2074, 2013.",
    "mla": "Ley, Timothy J., et al. “Genomic and Epigenomic Landscapes of Adult De Novo Acute Myeloid "
           "Leukemia.” New England Journal of Medicine, vol. 368, no. 22, 2013, pp. 2059–74.",
    "plos_old": "Ley TJ, Miller C, Ding L (2013) Genomic and epigenomic landscapes of adult de novo acute "
                "myeloid leukemia. N Engl J Med 368: 2059–2074.",
}


@pytest.mark.parametrize("style", sorted(STYLES))
def test_same_paper_reads_the_same_in_every_style(style):
    r = parse_reference_text(STYLES[style])
    assert r["title"].lower() == TITLE
    assert r["year"] == "2013"
    assert r["volume"] == "368"
    assert r["pages"].startswith("2059")
    assert "med" in r["journal"].lower()
    assert split_author(author_list(r["authors"])[0])[0] == "ley"


def test_cell_style_with_ampersand_free_and():
    r = parse_reference_text("Atallah, E., Schiffer, C.A., and Pinilla-Ibarz, J. (2021). Assessment of Outcomes "
                             "After Stopping Tyrosine Kinase Inhibitors: A Nonrandomized Clinical Trial. "
                             "JAMA Oncol. 7, 42–50.")
    assert r["journal"] == "JAMA Oncol" and r["volume"] == "7" and r["pages"] == "42-50"
    assert r["title"].endswith("Clinical Trial")


def test_journal_abbreviations_stay_in_the_journal():
    r = parse_reference_text("Ley, T. J., Miller, C. & Ding, L. Genomic landscapes in AML. "
                             "Proc. Natl Acad. Sci. USA 110, 1–9 (2013).")
    assert r["title"] == "Genomic landscapes in AML"
    assert r["journal"] == "Proc. Natl Acad. Sci. USA"


@pytest.mark.parametrize("text, journal, volume", [
    ("Hughes, A. & Yong, A. S. M. Immune Effector Recovery in Chronic Myeloid Leukemia and Treatment-Free "
     "Remission. Front Immunol 8, (2017).", "Front Immunol", "8"),
    ("Hughes, T. P., Yong, A. S. & Ross, D. M. The Evolution of Treatment-Free Remission. Blood (2025).", "Blood", ""),
    ("Hughes, A., and A. S. M. Yong. 2017. “Immune Effector Recovery in Chronic Myeloid Leukemia and "
     "Treatment-Free Remission”. Frontiers in Immunology 8.", "Frontiers in Immunology", "8"),
    ("Hughes, A., and Yong, A.S.M. (2017). Immune Effector Recovery in Chronic Myeloid Leukemia and "
     "Treatment-Free Remission. Front Immunol 8.", "Front Immunol", "8"),
])
def test_online_only_articles_without_pages(text, journal, volume):
    r = parse_reference_text(text)
    assert r["journal"] == journal and r["volume"] == volume
    assert r["title"].startswith(("Immune Effector", "The Evolution"))


def test_apostrophes_are_not_quotation_marks():
    r = parse_reference_text("O'Brien S, Smith J. Hodgkin's lymphoma outcomes in the 'real world' setting today. "
                             "Blood. 2010;115(3):1-9.")
    assert r["title"] == "Hodgkin's lymphoma outcomes in the 'real world' setting today"
    assert r["journal"] == "Blood" and r["parse_method"] == "vancouver"


def test_labelled_tail_year_is_not_a_page():
    r = parse_reference_text(STYLES["mla"])
    assert r["year"] == "2013"


def test_compound_surnames_written_surname_first():
    names = author_list("Pavlovsky, C., Vasconcelos Cordoba, B., San Miguel, J., & von Lilienfeld-Toal, M.")
    assert names == ["Pavlovsky C", "Vasconcelos Cordoba B", "San Miguel J", "von Lilienfeld-Toal M"]
    assert [split_author(n)[0] for n in names] == ["pavlovsky", "vasconcelos cordoba", "san miguel",
                                                   "von lilienfeld toal"]


def test_vancouver_author_lists_unchanged():
    assert author_list("Kok CH, Van de Donk NWCJ, Lim S-l, et al") == ["Kok CH", "Van de Donk NWCJ", "Lim S-l"]
    assert author_list("Kok, CH") == ["Kok CH"]


def test_title_found_inside_the_raw_text():
    text = STYLES["science"]
    assert title_in_text(TITLE, text) >= 0.95
    assert title_in_text("Treatment free remission in chronic myeloid leukaemia", text) < 0.6


def test_surname_at_start_skips_numbers_and_initials():
    assert surname_at_start("Ley", "12. T. J. Ley et al., Genomic ...")
    assert not surname_at_start("Miller", "T. J. Ley, C. Miller, Genomic ...")


def test_any_style_evidence_uses_the_raw_text():
    ref = {"title": "", "authors": "", "text": STYLES["science"]}
    cand = {"title": "Genomic and epigenomic landscapes of adult de novo acute myeloid leukemia.",
            "authors": "Ley TJ; Miller C; Ding L", "first_author": "Ley"}
    assert title_similarity_any_style(ref, cand) >= 0.95
    assert first_author_any_style(ref, cand) == "exact"


def test_text_terms_are_distinctive_words():
    terms = text_terms(STYLES["ieee"])
    assert 4 <= len(terms) <= 6
    assert not {"and", "vol", "the", "of"} & {t.lower() for t in terms}


@pytest.mark.parametrize("text, first, journal, volume, pages", [
    ("Li,H. and Durbin,R. (2009) Fast and accurate short read alignment with Burrows-Wheeler transform. "
     "Bioinformatics, 25, 1754–1760.", "li", "Bioinformatics", "25", "1754-1760"),
    ("Toubia,J. et al. (2025) TRanscriptome ANalysis of StratifiEd CohorTs (TRANSECT) enables automated "
     "assessment of global gene regulation. NAR Genom Bioinform., 7, lqaf041.", "toubia", "NAR Genom Bioinform", "7",
     "lqaf041"),
    ("Meghdadi,B. et al. (2026) Digital twins for in vivo metabolic flux estimations in patients with brain "
     "cancer. Cell Metab., 38, 228–246.e17.", "meghdadi", "Cell Metab", "38", "228-246.e17"),
    ("Li H, Durbin R. Fast and accurate short read alignment with Burrows-Wheeler transform. "
     "Bioinformatics 2009;25(14):1754–60.", "li", "Bioinformatics", "25", "1754-60"),
])
def test_bioinformatics_oup_layouts(text, first, journal, volume, pages):
    r = parse_reference_text(text)
    assert split_author(author_list(r["authors"])[0])[0] == first
    assert r["journal"] == journal and r["volume"] == volume and r["pages"] == pages


def test_no_space_after_surname_comma():
    names = author_list("Cullen,S.P., Brunet,M. and Martin,S.J.")
    assert [split_author(n)[0] for n in names] == ["cullen", "brunet", "martin"]


@pytest.mark.parametrize("text, first, journal, volume, issue, pages", [
    ("Ley, T.J.; Miller, C.; Ding, L. Genomic and Epigenomic Landscapes of Adult De Novo Acute Myeloid Leukemia. "
     "N. Engl. J. Med. 2013, 368, 2059–2074.", "ley", "N. Engl. J. Med", "368", "", "2059-2074"),
    ("1. van de Donk, N.W.C.J.; Pawlyn, C.; Yong, K.L. Multiple Myeloma. Lancet 2021, 397, 410–27.",
     "van de donk", "Lancet", "397", "", "410-27"),
    ("Ștefănigă, S.A.; Cordoș, A.A.; Ivascu, T. Advancing Precision Oncology with Digital and Virtual Twins: "
     "A Scoping Review. Cancers 2024, 16(4), 781.", "stefaniga", "Cancers", "16", "4", "781"),
    ("Pérez-Benito Á; García-Aznar, J.M.; Pérez, M. Patient-specific Prostate Tumour Growth Simulation. "
     "Front. Physiol. 2024, 15, 1421591.", "perez benito", "Front. Physiol", "15", "", "1421591"),
])
def test_mdpi(text, first, journal, volume, issue, pages):
    r = parse_reference_text(text)
    assert r["parse_method"] == "mdpi"
    assert split_author(author_list(r["authors"])[0])[0] == first
    assert (r["journal"], r["volume"], r["issue"], r["pages"]) == (journal, volume, issue, pages)


def test_journal_left_in_title_loses_to_a_clean_split():
    r = parse_reference_text("Toubia, J.; Kok, C.H.; Yeung, D.T. TRANSECT enables automated assessment of global "
                             "gene regulation. NAR Genom. Bioinform. 2025, 7, lqaf041.")
    assert r["title"].endswith("gene regulation") and r["journal"] == "NAR Genom. Bioinform"
