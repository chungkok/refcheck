"""Drive app.py headlessly with the mock PubMed client and a fake upload.
    python tests/test_app.py path/to/file.(docx|pdf|...)"""
import io, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
import streamlit as st
from streamlit.testing.v1 import AppTest
import refcheck.pubmed as pm
from fixtures import MockClient

DOC = Path(sys.argv[1])


class Upload(io.BytesIO):
    name = DOC.name


st.file_uploader = lambda *a, **k: Upload(DOC.read_bytes())
pm.PubMedClient = lambda *a, **k: MockClient()


def btn(at, text):
    return next(b for b in at.button if text in b.label)


at = AppTest.from_file(str(Path(__file__).parent.parent / "app.py"), default_timeout=120)
at.run()
at.sidebar.text_input[0].set_value("me@example.org").run()
btn(at, "Check all references").click().run()
print("metrics:", [(m.label, m.value) for m in at.metric])
print("caption:", [c.value for c in at.caption if "go to EndNote" in c.value])

# one by one: first reference, accept best match
print("selected:", at.selectbox[0].value, "| status line:", [m.value[:90] for m in at.markdown if "Status" in m.value])
print("comparison table rows:", len(at.dataframe[0].value) if at.dataframe else 0)
btn(at, "✔ Use PMID").click().run()
print("after accept:", [c.value for c in at.caption if "go to EndNote" in c.value])

# jump to an unmatched reference and type a PMID
at.radio(key="flt").set_value("Not in PubMed").run()
num = at.selectbox[0].value
at.text_input(key=f"oth{num}").set_value("40481322").run()
print(f"typed PMID for ref {num}:", [m.value[:150] for m in at.markdown if "Going to EndNote" in m.value])
print("shows chosen record:", [m.value[:60] for m in at.markdown if "PMID you chose" in m.value],
      "| rows:", len(at.dataframe[0].value) if at.dataframe else 0)
at.radio(key="flt").set_value("All").run()
at.tabs  # table tab is rendered too
print("table use PMID:", at.get("arrow_data_frame")[0].value["use PMID"].tolist() if at.get("arrow_data_frame") else "n/a")

btn(at, "Build files").click().run()
print("subheader:", [h.value for h in at.subheader][-1])
print("downloads:", [d.label for d in at.get("download_button")])
print("warnings:", [w.value[:100] for w in at.warning])
print("exceptions:", [e.value for e in at.exception])
