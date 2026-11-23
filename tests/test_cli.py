"""Command line: single file (old usage), batch folder, round trip from a saved report."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

import refcheck_cli  # noqa: E402
from fixtures import MockClient  # noqa: E402
from make_manuscript import REFS, build  # noqa: E402
from word_tools import simulate_endnote_update  # noqa: E402


def patch(monkeypatch):
    monkeypatch.setattr(refcheck_cli, "PubMedClient", lambda *a, **k: MockClient())


def test_single_file_old_usage(tmp_path, monkeypatch, capsys):
    patch(monkeypatch)
    src = tmp_path / "ms.docx"
    build(src)
    assert refcheck_cli.main([str(src), "--email", "a@b.c", "--outdir", str(tmp_path / "o"),
                              "--pmid", "6=40481322"]) == 0
    out = capsys.readouterr().out
    assert "Integrity:" in out and "Only bibliographic search terms and identifiers were sent" in out
    for suffix in ("_endnote.ris", "_endnote.xml", "_endnote_ready.docx", "_integrity_report.json",
                   "_integrity_report.html", "_pubmed_report.xlsx", "_pmids.txt", "_refcheck.zip"):
        assert (tmp_path / "o" / f"ms{suffix}").exists(), suffix


def test_batch_folder(tmp_path, monkeypatch, capsys):
    patch(monkeypatch)
    folder = tmp_path / "in"
    folder.mkdir()
    build(folder / "a.docx")
    (folder / "b.txt").write_text("References\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(REFS, 1)))
    (folder / "~$lock.docx").write_bytes(b"")            # Word lock file is skipped
    assert refcheck_cli.main([str(folder), "--email", "a@b.c", "--outdir", str(tmp_path / "o")]) == 0
    assert "Batch finished: 2 of 2" in capsys.readouterr().out
    assert (tmp_path / "o" / "a_endnote_ready.docx").exists()
    assert (tmp_path / "o" / "b_endnote.ris").exists() and not (tmp_path / "o" / "b_endnote_ready.docx").exists()


def test_audit_only_writes_no_word_copy(tmp_path, monkeypatch):
    patch(monkeypatch)
    src = tmp_path / "ms.docx"
    build(src)
    refcheck_cli.main([str(src), "--email", "a@b.c", "--outdir", str(tmp_path / "o"), "--audit-only"])
    assert not (tmp_path / "o" / "ms_endnote_ready.docx").exists()
    rep = json.loads((tmp_path / "o" / "ms_integrity_report.json").read_text())
    audit = rep["citation_audit"]
    assert audit is not None and audit["citations_converted"] > 0 and "uncited_references" in audit


def test_roundtrip_from_report(tmp_path, monkeypatch, capsys):
    patch(monkeypatch)
    src = tmp_path / "ms.docx"
    build(src)
    refcheck_cli.main([str(src), "--email", "a@b.c", "--outdir", str(tmp_path / "o")])
    rep = json.loads((tmp_path / "o" / "ms_integrity_report.json").read_text())
    labels = {}
    for r in rep["references"]:
        labels.setdefault(r["final"]["endnote_record"], []).append(str(r["ref_no"]))
    labels = {k: ", ".join(v) for k, v in labels.items()}
    good, bad = tmp_path / "good.docx", tmp_path / "bad.docx"
    simulate_endnote_update(tmp_path / "o" / "ms_endnote_ready.docx", good, labels)
    simulate_endnote_update(tmp_path / "o" / "ms_endnote_ready.docx", bad, labels, leave_one=True)
    report = str(tmp_path / "o" / "ms_integrity_report.json")
    assert refcheck_cli.main(["--roundtrip", report, str(good)]) == 0
    assert refcheck_cli.main(["--roundtrip", report, str(bad)]) == 1
