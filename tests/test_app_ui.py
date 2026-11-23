"""Drive app.py headlessly with the mock PubMed client and a fake upload.

    pytest tests/test_app_ui.py                    (synthetic manuscript and text file)
    python tests/test_app_ui.py path/to/file.docx  (any file, prints a trace)
"""
import io
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

import refcheck.pubmed as pm  # noqa: E402
from fixtures import MockClient  # noqa: E402


def btn(at, text):
    return next(b for b in at.button if text in b.label)


def drive(doc: Path, show=print):
    """Upload, check, accept one match, type one PMID, build. Returns the AppTest."""

    class Upload(io.BytesIO):
        name = doc.name

    real_uploader, real_client = st.file_uploader, pm.PubMedClient
    # only the manuscript uploader receives the file; the round trip uploader stays empty
    st.file_uploader = lambda label, *a, **k: Upload(doc.read_bytes()) if label.startswith("Manuscript") else None
    pm.PubMedClient = lambda *a, **k: MockClient()
    try:
        at = AppTest.from_file(str(HERE.parent / "app.py"), default_timeout=120)
        at.run()
        at.sidebar.text_input[0].set_value("me@example.org").run()
        btn(at, "Check all references").click().run()
        show("metrics:", [(m.label, m.value) for m in at.metric])

        btn(at, "✔ Use PMID").click().run()
        at.radio(key="flt").set_value("Not in PubMed").run()
        num = at.selectbox[0].value
        at.text_input(key=f"oth{num}").set_value("40481322").run()
        show("after typing:", [m.value[:120] for m in at.markdown if "Going to EndNote" in m.value])

        btn(at, "Build files").click().run()
        show("downloads:", [d.label for d in at.get("download_button")])
        show("warnings:", [w.value[:100] for w in at.warning])
        show("exceptions:", [e.value for e in at.exception])
        return at
    finally:
        st.file_uploader, pm.PubMedClient = real_uploader, real_client


def _quiet(*a):
    pass


def test_review_and_build_word(tmp_path):
    from make_manuscript import build
    from refcheck.privacy import pending_temp_folders
    doc = tmp_path / "ms.docx"
    build(doc)
    before = pending_temp_folders()
    at = drive(doc, show=_quiet)
    assert not at.exception, [e.value for e in at.exception]
    labels = [d.label for d in at.get("download_button")]
    assert "Word copy for EndNote" in labels and "EndNote import (.ris)" in labels
    assert "Integrity report (.html)" in labels and "Integrity report (.json)" in labels
    assert any("5. Check the result after EndNote" in h.value for h in at.subheader)
    # privacy: the app run left no temporary folder behind
    assert pending_temp_folders() == before


def test_review_and_build_text(tmp_path):
    from make_manuscript import REFS
    doc = tmp_path / "refs.txt"
    doc.write_text("References\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(REFS, 1)))
    at = drive(doc, show=_quiet)
    assert not at.exception, [e.value for e in at.exception]
    labels = [d.label for d in at.get("download_button")]
    assert "Word copy for EndNote" not in labels and "Report (.xlsx)" in labels


if __name__ == "__main__":
    drive(Path(sys.argv[1]))
