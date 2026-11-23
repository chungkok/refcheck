"""Phase 3: notes, existing citation fields, citation audit, structure, RIS, round trip."""
import sys
from pathlib import Path

import pytest
from docx import Document

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from fixtures import MockClient  # noqa: E402
from make_manuscript import build  # noqa: E402
from word_tools import add_endnote_field, add_mendeley_control, add_notes, simulate_endnote_update  # noqa: E402
from refcheck.annotate import annotate_docx, document_structure  # noqa: E402
from refcheck.export import read_ris, validate_ris  # noqa: E402
from refcheck.extract import extract_references  # noqa: E402
from refcheck.pipeline import build_outputs, default_decisions, run_matching  # noqa: E402
from refcheck.roundtrip import validate_roundtrip  # noqa: E402

OPTS = dict(brackets=True, superscript=True, parentheses=False)


@pytest.fixture
def manuscript(tmp_path):
    src = tmp_path / "ms.docx"
    build(src)
    d = Document(str(src))
    p = d.paragraphs[1]
    p.add_run(" Existing EndNote ")
    add_endnote_field(p, "[3]")
    p.add_run(" and Mendeley ")
    add_mendeley_control(p, "[2]")
    d.save(str(src))
    add_notes(src, footnotes=["See the original method [4]."], endnotes=["Also reviewed in [5,6]."])
    return src


def run_all(src, tmp_path, **kw):
    ex = extract_references(src)
    refs = ex["refs"]
    client = MockClient()
    res = run_matching(refs, client)
    dec = default_decisions(refs, res)
    out = build_outputs(ex, refs, res, dec, client, tmp_path / "out", "ms", annotate_opts=OPTS, **kw)
    return ex, refs, res, dec, out


def test_notes_converted_and_existing_fields_preserved(manuscript, tmp_path):
    ex, refs, res, dec, out = run_all(manuscript, tmp_path)
    log = out["citation_log"]
    locs = {e["location"] for e in log if e["replaced_with"]}
    assert {"body", "footnote", "endnote"} <= locs
    assert out["existing_fields"]["endnote"] == 1 and out["existing_fields"]["mendeley"] == 1
    # the two managed citations were not rewritten; they are logged as existing citations, so the
    # audit counts them as citing references 3 and 2
    managed = [e for e in log if e["original"] in ("[3]", "[2]") and e["paragraph"] == "P2"]
    assert managed and all(e.get("existing") and not e["replaced_with"] for e in managed)
    assert {e["form"] for e in managed} == {"existing endnote field", "existing mendeley field"}
    after = document_structure(out["paths"]["docx"])
    assert after["fields"]["endnote"] == 1 and after["footnotes"] == 1 and after["endnotes"] == 1
    assert out["changes"]["structure_preserved"] is True
    text = "\n".join(p.text for p in Document(str(out["paths"]["docx"])).paragraphs)
    assert "Existing EndNote [3]" in text


def test_original_is_never_overwritten(manuscript):
    with pytest.raises(ValueError):
        annotate_docx(manuscript, manuscript, {1: 1}, {1: ("A", "2000")})


def test_citation_audit(manuscript, tmp_path):
    ex, refs, res, dec, out = run_all(manuscript, tmp_path)
    a = out["audit"]
    assert [c["missing"] for c in a["citations_without_reference"]] == [[12]]
    assert a["duplicate_references"] == [dict(reference=8, same_as=2)]
    assert a["mixed_citation_styles"]["others"]            # brackets and superscripts both used
    assert a["citations_by_location"]["footnote"] == 1
    assert set(a["times_cited"]) == set(range(1, 9))
    assert any("not in the reference list: 12" in f for f in out["audit_findings"])
    assert any(m["refs"] == [2, 3, 4, 5] for m in a["mapping"])


def test_out_of_order_first_citation():
    from refcheck.audit import citation_audit
    log = [dict(paragraph="P1", original=o, replaced_with="x", refs=r, problem="", form="brackets")
           for o, r in (("[1]", "1"), ("[2]", "2"), ("[5]", "5"), ("[3,4]", "3,4"))]
    a = citation_audit(log, [dict(num=n) for n in range(1, 6)])
    assert a["first_citation_out_of_order"] == [5] and a["uncited_references"] == []


def test_ris_is_valid_and_validation_catches_errors(manuscript, tmp_path):
    ex, refs, res, dec, out = run_all(manuscript, tmp_path)
    assert out["ris_problems"] == []
    recs = read_ris(out["paths"]["ris"])
    assert recs[1]["LB"] == ["2, 8"] and recs[1]["AN"] == ["35357562"]
    bad = tmp_path / "bad.ris"
    bad.write_text("TY  - JOUR\nAU  - Smith J\nPY  - 20\nLB  - 1\nER  - \n")
    probs = validate_ris(bad)
    assert any("no title" in p for p in probs) and any("four digits" in p for p in probs)
    assert any("Last, First" in p for p in probs)
    xml = out["paths"]["endnote_xml"].read_text()
    assert "<label><style" in xml and "35357562" in xml and xml.count("<record>") == out["n_records"]


@pytest.mark.parametrize("data_field", [False, True, "nested"])
def test_roundtrip_ok(manuscript, tmp_path, data_field):
    ex, refs, res, dec, out = run_all(manuscript, tmp_path)
    labels = {}
    for r in refs:
        labels.setdefault(out["ref_to_rec"][r["num"]], []).append(str(r["num"]))
    labels = {k: ", ".join(v) for k, v in labels.items()}
    final = tmp_path / "final.docx"
    simulate_endnote_update(out["paths"]["docx"], final, labels, data_field=data_field)
    rt = validate_roundtrip(final, refs, out["citation_log"], out["decisions"], out["records"],
                            preexisting=out["existing_fields"]["endnote"])
    assert rt["ok"], rt
    assert rt["endnote_citation_fields"] == rt["expected_citations"] + 1   # plus the pre-existing one
    assert rt["bibliography_fields"] == 1


def test_roundtrip_detects_problems(manuscript, tmp_path):
    ex, refs, res, dec, out = run_all(manuscript, tmp_path)
    labels = {out["ref_to_rec"][r["num"]]: str(r["num"]) for r in refs}
    final = tmp_path / "final.docx"
    simulate_endnote_update(out["paths"]["docx"], final, labels, tamper={1: 3}, leave_one=True)
    rt = validate_roundtrip(final, refs, out["citation_log"], out["decisions"], out["records"],
                            preexisting=out["existing_fields"]["endnote"])
    assert not rt["ok"]
    assert rt["leftover_temporary_citations"] and rt["citations_missing"] and rt["citations_unexpected"]


def test_first_citation_order_uses_document_position(tmp_path):
    """Regression: citations in one paragraph were logged right to left."""
    from docx import Document as D
    src = tmp_path / "o.docx"
    d = D()
    d.add_paragraph("First [1] then [2] then [3].")
    d.add_heading("References", 1)
    for i in range(1, 4):
        d.add_paragraph(f"{i}. Author A. A title number {i} here. J Test. 2020;{i}:1-2.")
    d.save(str(src))
    ex = extract_references(src)
    refs = ex["refs"]
    client = MockClient()
    res = run_matching(refs, client)
    out = build_outputs(ex, refs, res, default_decisions(refs, res), client, tmp_path / "out", "o",
                        annotate_opts=OPTS)
    assert out["audit"]["first_citation_out_of_order"] == []


def test_author_suffixes_stay_with_their_name():
    from refcheck.export import _split_authors
    assert _split_authors("Miller K, Roberts WJ 2nd, Smith J Jr, Doe A") == [
        "Miller, K", "Roberts, WJ, 2nd", "Smith, J, Jr", "Doe, A"]
    assert _split_authors("Miller K, Roberts WJ, 2nd, Doe A") == ["Miller, K", "Roberts, WJ, 2nd", "Doe, A"]
