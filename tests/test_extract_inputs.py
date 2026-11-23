"""Input formats and calibration script coverage."""
import csv
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

from make_manuscript import REFS  # noqa: E402
from refcheck.extract import extract_references  # noqa: E402


def test_csv_with_citation_column(tmp_path):
    p = tmp_path / "refs.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ref_no", "citation", "pmid"])
        for i, r in enumerate(REFS[:3], 1):
            w.writerow([i, r, "35357562" if i == 2 else ""])
    ex = extract_references(p)
    assert [r["num"] for r in ex["refs"]] == [1, 2, 3]
    assert ex["refs"][1]["pmid_hint"] == "35357562" and ex["refs"][0]["journal"] == "Neuro Oncol"


def test_tsv_and_xlsx_with_separate_columns(tmp_path):
    from openpyxl import Workbook
    rows = [["title", "authors", "journal", "year", "volume", "pages", "doi"],
            ["ROBIN: a unified nanopore assay", "Deacon S", "Neuro Oncol", "2025", "27", "2035-2046",
             "10.1093/neuonc/noaf103"]]
    t = tmp_path / "refs.tsv"
    t.write_text("\n".join("\t".join(r) for r in rows), encoding="utf-8")
    x = tmp_path / "refs.xlsx"
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    wb.save(x)
    for p in (t, x):
        (r,) = extract_references(p)["refs"]
        assert (r["title"], r["year"], r["volume"], r["doi"]) == (
            "ROBIN: a unified nanopore assay", "2025", "27", "10.1093/neuonc/noaf103")


def test_rtf_and_markdown(tmp_path):
    rtf = tmp_path / "refs.rtf"
    rtf.write_text(r"{\rtf1\ansi References\par 1. " + REFS[2] + r"\par 2. " + REFS[3] + r"\par}", encoding="utf-8")
    md = tmp_path / "refs.md"
    md.write_text("# Paper\n\nText [1].\n\n## References\n\n1. " + REFS[2] + "\n2. " + REFS[3] + "\n")
    for p in (rtf, md):
        refs = extract_references(p)["refs"]
        assert len(refs) == 2 and refs[1]["journal"] == "Neuro Oncol", p.suffix


def test_custom_heading_and_unnumbered_list(tmp_path):
    p = tmp_path / "refs.txt"
    p.write_text("Intro.\n\nLiterature\n\n" + REFS[0] + "\n" + REFS[1] + "\n\nFigure legends\nFig 1.")
    with pytest.raises(ValueError):
        extract_references(p)                         # default headings do not match
    ex = extract_references(p, heading="Literature")
    assert len(ex["refs"]) == 2 and "not numbered" in ex["warnings"][0]


def test_pdf_text_layer(tmp_path):
    rl = pytest.importorskip("reportlab")  # noqa: F841 - only needed to build the test PDF
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    st = getSampleStyleSheet()
    pdf = tmp_path / "ms.pdf"
    story = [Paragraph("Body [1].", st["Normal"]), Paragraph("References", st["Heading2"])]
    story += [Paragraph(f"{i}. {r}", st["Normal"]) for i, r in enumerate(REFS, 1)]
    SimpleDocTemplate(str(pdf), pagesize=A4).build(story)
    refs = extract_references(pdf)["refs"]
    assert len(refs) == len(REFS) and refs[4]["journal"] == "Nat Cancer"


