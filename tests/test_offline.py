"""Offline tests (no network). Run:  python -m pytest tests  or  python tests/test_offline.py"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from docx import Document  # noqa: E402

from fixtures import MockClient  # noqa: E402
from make_manuscript import build  # noqa: E402
from refcheck.extract import extract_references, parse_reference_text  # noqa: E402
from refcheck.pipeline import apply_overrides, build_outputs, default_decisions, run_matching  # noqa: E402


def test_parser_keeps_title_and_journal_apart():
    r = parse_reference_text(
        "Deacon S, Cahyani I, et al. ROBIN: A unified nanopore-based assay integrating intraoperative "
        "methylome classification and next-day comprehensive profiling for ultra-rapid tumor diagnosis. "
        "Neuro Oncol. 2025;27(8):2035-2046.")
    assert r["title"].endswith("ultra-rapid tumor diagnosis")
    assert (r["journal"], r["year"], r["volume"], r["issue"], r["pages"]) == ("Neuro Oncol", "2025", "27", "8", "2035-2046")


STYLE_CASES = [
    # (reference text, expected (journal, year, volume, pages))
    ("IMS/IMWG consensus recommendations on the definition of high risk multiple myeloma. "
     "J Clin Oncol 2025. doi:10.1200/JCO-24-01893 (PMID 40489728).", ("J Clin Oncol", "2025", "", "")),
    ("Ewing AD, et al. Combining tumor genome simulation with crowdsourcing to benchmark somatic single "
     "nucleotide variant detection. Nat Methods 2015;12:623 to 630 [verify].", ("Nat Methods", "2015", "12", "623-630")),
    ("Kazandjian D. Multiple Myeloma Epidemiology and Survival: A Unique Malignancy. Semin Oncol (2016) "
     "43(6):676\u201381. Epub 20161110. doi: 10.1053/j.seminoncol.2016.11.004.", ("Semin Oncol", "2016", "43", "676-81")),
    ("Hou Q, Li X, Ma H, Fu D, Liao A. A Systematic Epidemiological Trends Analysis Study in Global Burden "
     "of Multiple Myeloma and 29 Years Forecast. Sci Rep. 15. England(2025). p. 2204.", ("Sci Rep", "2025", "15", "2204")),
    ("Sun S, Huang Z, Tang Y. Establishment of a Multimodal Prognostic Prediction Model. Turk J Haematol (2026). "
     "Epub 20260716. doi: 10.4274/tjh.galenos.2026.33678.", ("Turk J Haematol", "2026", "", "")),
    ("Martin M, Patterson M, Garg SO, et al. WhatsHap: fast and accurate read-based phasing. 2016 "
     "https://doi.org/10.1101/085050.", ("", "2016", "", "")),
    ("Yuan D, Jugas R, et al. crossNN is an explainable framework for cross-platform DNA methylation-based "
     "classification of tumors. Nat Cancer. 2025;6(7):1283\u201394. h t t p s: / / d o i. o r g / 1 0. 1 0 3 8 / "
     "s 4 3 0 1 8 - 0 2 5 - 0 0 9 7 6 - 5.", ("Nat Cancer", "2025", "6", "1283-94")),
]


def test_citation_styles():
    for text, (j, y, v, pg) in STYLE_CASES:
        r = parse_reference_text(text)
        assert (r["journal"], r["year"], r["volume"], r["pages"]) == (j, y, v, pg), (text, r)
        assert len(r["title"]) > 20 and not r["parse_note"], (text, r)
    r = parse_reference_text(STYLE_CASES[-1][0])
    assert r["doi"] == "10.1038/s43018-025-00976-5"


def test_glued_guard():
    import re
    from refcheck.annotate import GLUED_RE, glued_ok
    text = ("shifted toward classification1. diagnosis1,2. IDH1 and TP53 in CNS5 on hg19 chr17 "
            "H3K27me3 2-hydroxyglutarate91. beyond WHO grade37. within 72 hours83.")
    word = lambda m: re.search(r"(\S+)$", text[: m.start()]).group(1).split("-")[-1] + m.group(1)
    verdict = {word(m): glued_ok(text, m) for m in GLUED_RE.finditer(text)}
    accepted = sorted(k for k, v in verdict.items() if v is True)
    flagged = [k for k, v in verdict.items() if isinstance(v, str)]
    assert accepted == ["classification1", "diagnosis1,2", "hours83", "hydroxyglutarate91"], accepted
    assert len(flagged) == 1 and "grade37" in flagged[0]


def test_line_numbered_pdf_text():
    from refcheck.extract import drop_repeated_lines, strip_line_numbers
    lines = []
    for i in range(40):
        lines += [f"Line of text {i}{500 + i}", "In review"]
    lines += ["References540", "1. Smith J. A title. Blood. 2020;1:2-3.541", "plain line ending 2020"]
    out = drop_repeated_lines(strip_line_numbers(lines))
    assert out[0] == "Line of text 0" and out[39] == "Line of text 39", out[:3]
    assert "References" in out and "In review" not in out
    assert "1. Smith J. A title. Blood. 2020;1:2-3." in out and "plain line ending 2020" in out
    assert strip_line_numbers(["Blood. 2020;1:2-3.", "Accessed 2025"]) == ["Blood. 2020;1:2-3.", "Accessed 2025"]


def test_end_to_end(tmp_path=None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    for word_numbering in (False, True):
        src = tmp / f"ms_{word_numbering}.docx"
        build(src, word_numbering)
        ex = extract_references(src)
        refs = ex["refs"]
        assert len(refs) == 8
        client = MockClient()
        res = run_matching(refs, client)
        assert [res[n]["status"] for n in range(1, 9)] == ["AUTO_MATCH"] * 5 + ["NO_MATCH"] * 2 + ["AUTO_MATCH"]
        assert res[2]["best"]["pmid"] == res[8]["best"]["pmid"] == "35357562"   # Rapid-CNS2
        assert res[5]["best"]["pmid"] == "40481322"                            # crossNN
        dec = apply_overrides(default_decisions(refs, res), res, {7: ""})
        out = build_outputs(ex, refs, res, dec, client, tmp / "out", src.stem, reviewed={1: True},
                            annotate_opts=dict(brackets=True, superscript=True, parentheses=False))
        assert [r["checked_by_you"] for r in out["rows"]][:2] == ["yes", ""]
        assert out["paths"]["pmids"].read_text().split() == ["41092086", "35357562", "40392954",
                                                             "42152226", "40481322"]
        assert out["n_records"] == 7                     # refs 2 and 8 share a record
        assert out["ref_to_rec"][8] == out["ref_to_rec"][2] == 2
        ris = out["paths"]["ris"].read_text(encoding="utf-8")
        assert ris.count("ER  - ") == 7 and "AN  - 35357562" in ris and "EP  - 1294" in ris
        text = "\n".join(p.text for p in Document(out["paths"]["docx"]).paragraphs)
        assert "{Price, 2025 #1}" in text
        assert "{Patel, 2022 #2; Deacon, 2025 #3; Brat, 2026 #4; Yuan, 2025 #5}" in text
        assert "feasible{Deacon, 2025 #3; Brat, 2026 #4}" in text
        assert "10" + "6 cells" in text and "mm2" in text   # exponent and unit untouched
        assert "[12]" in text                              # out of range left alone
        assert "Acta Neuropathol. 2022" not in text        # old reference list removed
        assert "adapted from {Patel, 2022 #2}" in text      # figure legends after the list
        assert out["reference_style"]["family"] and "detected_style" in out["rows"][0]
        assert "detected_style" in out["paths"]["csv"].read_text(encoding="utf-8").splitlines()[0]


if __name__ == "__main__":
    test_parser_keeps_title_and_journal_apart()
    test_citation_styles()
    test_glued_guard()
    test_line_numbered_pdf_text()
    test_end_to_end()
    print("all offline tests passed")
