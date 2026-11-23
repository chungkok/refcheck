"""Citation style detection per reference and per manuscript, and the mixed style warning."""
import pytest

from refcheck.styles import FAMILIES, detect_reference_style, manuscript_style

FAMILIES_V = FAMILIES["vancouver"]

CASES = {
    "apa": ("Cullen, S. P., Brunet, M., & Martin, S. J. (2010). Granzymes in cancer and immunity. "
            "Cell Death and Differentiation, 17(4), 616–23.", "APA"),
    "nature": ("Cullen, S. P., Brunet, M. & Martin, S. J. Granzymes in cancer and immunity. Cell Death Differ "
               "17, 616–23 (2010).", "Nature"),
    "science": ("S. P. Cullen, M. Brunet, S. J. Martin, Granzymes in cancer and immunity. Cell Death Differ 17, "
                "616–23 (2010).", "Science"),
    "cell": ("Cullen, S.P., Brunet, M., and Martin, S.J. (2010). Granzymes in cancer and immunity. Cell Death "
             "Differ 17, 616–23.", "Cell"),
    "frontiers": ("Cullen, S. P., Brunet, M., and Martin, S. J. (2010). Granzymes in cancer and immunity. Cell "
                  "Death Differ 17, 616–23.", "Frontiers"),
    "lancet": ("Cullen SP, Brunet M, Martin SJ. Granzymes in cancer and immunity. Cell Death Differ 2010; 17: "
               "616–23.", "Lancet"),
    "ama": ("Cullen SP, Brunet M, Martin SJ. Granzymes in cancer and immunity. Cell Death Differ. "
            "2010;17(4):616-23.", "AMA"),
    "vancouver": ("Cullen SP, Brunet M, Martin SJ. Granzymes in cancer and immunity. Cell Death Differ. "
                  "2010;17(4):616–23.", "Vancouver (NLM)"),
    "plos": ("Cullen SP, Brunet M, Martin SJ. Granzymes in cancer and immunity. Cell Death Differ. 2010;17: "
             "616–23.", "PLOS"),
    "nejm": ("Cullen SP, Brunet M, Martin SJ. Granzymes in cancer and immunity. Cell Death Differ "
             "2010;17(4):616–23.", "NEJM or Bioinformatics"),
    "harvard": ("Cullen, S.P., Brunet, M. and Martin, S.J. (2010) “Granzymes in cancer and immunity”, Cell Death "
                "and Differentiation, 17(4), pp. 616–23.", "Harvard"),
    "chicago": ("Cullen, S. P., M. Brunet, and S. J. Martin. 2010. “Granzymes in Cancer and Immunity”. Cell Death "
                "and Differentiation 17 (4): 616–23.", "Chicago author date"),
    "ieee": ("S. P. Cullen, M. Brunet, and S. J. Martin, “Granzymes in cancer and immunity”, Cell Death Differ, "
             "vol. 17, no. 4, pp. 616–23, 2010.", "IEEE"),
    "mla": ("Cullen, S. P., et al. “Granzymes in Cancer and Immunity”. Cell Death and Differentiation, vol. 17, "
            "no. 4, 2010, pp. 616–23.", "MLA"),
    "mdpi": ("Cullen, S.P.; Brunet, M.; Martin, S.J. Granzymes in Cancer and Immunity. Cell Death Differ 2010, 17, "
             "616–23.", "MDPI"),
    "oup_legacy": ("Cullen,S.P., Brunet,M. and Martin,S.J. (2010) Granzymes in cancer and immunity. Cell Death "
                   "Differ., 17, 616–23.", "Bioinformatics before 2023"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_each_style_is_recognised(name):
    text, style = CASES[name]
    d = detect_reference_style(text)
    assert d["style"] == style, d
    assert d["evidence"]


def test_single_initials_cannot_split_cell_from_frontiers():
    d = detect_reference_style("Palumbo, A., and Anderson, K. (2011). Multiple myeloma. N Engl J Med 364, 1046–60.")
    assert d["family"] == "cell" and d["style"] == "Cell / Frontiers"


def test_online_only_vancouver_is_a_tie():
    d = detect_reference_style("Hughes TP, Yong AS, Ross DM. The Evolution of Treatment-Free Remission. Blood 2025.")
    assert d["family"] == "vancouver" and " / " in d["style"]


def test_numbers_dois_and_particles():
    d = detect_reference_style("12.van de Donk, N. W. C. J., Pawlyn, C., & Yong, K. L. (2021). Multiple myeloma. "
                               "Lancet, 397(10272), 410–27. https://doi.org/10.1016/S0140-6736(21)00135-5")
    assert d["family"] == "apa"


def test_unpunctuated_text_is_not_recognised():
    d = detect_reference_style("PalumboA AndersonK Multiple myelomo N Engl J Med 2011 364 1046-60")
    assert d["family"] == "unknown"


def _ms(texts):
    return manuscript_style([dict(num=i + 1, text=t) for i, t in enumerate(texts)])


def test_one_style_manuscript_has_no_warning():
    res = _ms([CASES["ama"][0]] * 30)
    assert res["family"] == "vancouver" and res["style_guess"] == "AMA" and not res["mixed"]


def test_mixed_styles_warn_and_list_the_references():
    texts = [CASES["vancouver"][0]] * 30
    for k in (5, 6, 7, 20):
        texts[k] = CASES["apa"][0]
    res = _ms(texts)
    assert res["mixed"] and res["minority"][0]["references"] == [6, 7, 8, 21]
    assert "6–8, 21" in res["message"] and "do not show" in res["message"]


def test_two_odd_references_are_below_the_rule():
    texts = [CASES["vancouver"][0]] * 30
    texts[3] = texts[9] = CASES["apa"][0]
    assert not _ms(texts)["mixed"]


def test_bmj_resolves_over_the_manuscript():
    bmj = "Cullen SP, Brunet M, Martin SJ. Granzymes in cancer and immunity. Cell Death Differ. 2010;17:616–23."
    assert detect_reference_style(bmj)["style"] == "BMJ / Vancouver (NLM)"
    assert _ms([bmj] * 8)["style_guess"] == "BMJ"


def test_report_carries_the_style(tmp_path):
    from refcheck.report import _style
    assert _style(CASES["mdpi"][0])["style"] == "MDPI"


def test_mixed_warning_reaches_the_outputs(tmp_path):
    from tests.fixtures import MockClient
    from refcheck.pipeline import build_outputs, default_decisions, run_matching
    texts = [CASES["vancouver"][0].replace("Granzymes", f"Granzymes {i}") for i in range(12)]
    for k in (2, 5, 8):
        texts[k] = CASES["apa"][0]
    src = tmp_path / "refs.txt"
    src.write_text("References\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts)) + "\n", encoding="utf-8")
    from refcheck.extract import extract_references
    ex = extract_references(str(src))
    refs = ex["refs"]
    client = MockClient()
    res = run_matching(refs, client)
    out = build_outputs(ex, refs, res, default_decisions(refs, res), client, tmp_path / "out", "refs",
                        annotate=False)
    assert out["reference_style"]["mixed"]
    assert any("mixed citation styles" in w and "3, 6, 9" in w for w in out["warnings"])
    assert {r["detected_style_family"] for r in out["rows"]} == {"APA", FAMILIES_V}
