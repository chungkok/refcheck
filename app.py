"""RefCheck: verify a manuscript's references against PubMed and prepare them for EndNote.

Run:   streamlit run app.py
"""

import hashlib
import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from refcheck.annotate import detect_citation_forms, suggested_options
from refcheck.extract import extract_references
from refcheck.pipeline import apply_overrides, build_outputs, default_decisions, run_matching
from refcheck.pubmed import PubMedClient
from refcheck.review import compare_fields

st.set_page_config(page_title="RefCheck", page_icon="📚", layout="wide")
ss = st.session_state
EDIT_COLS = ["num", "authors", "title", "journal", "year", "volume", "issue", "pages", "doi", "pmid_hint", "text"]
STATUS_ICON = {"AUTO_MATCH": "🟢 matched", "REVIEW": "🟡 check", "NO_MATCH": "🔴 no match",
               "ERROR": "⚠️ error", "MANUAL": "🔵 manual"}

# ------------------------------------------------------------- sidebar ----
with st.sidebar:
    st.header("Settings")
    email = st.text_input("Your email (NCBI requires one)", value=ss.get("email", os.getenv("NCBI_EMAIL", "")))
    ss.email = email
    api_key = st.text_input("NCBI API key (optional, 3x faster)", value=os.getenv("NCBI_API_KEY", ""),
                            type="password")
    heading = st.text_input("Reference section heading", value="",
                            placeholder="References (auto)",
                            help="Only needed if your section is called something unusual.")
    st.subheader("EndNote")
    use_recnum = st.checkbox("Include EndNote record numbers", value=True,
                             help="Gives an exact link to each record. Needs the RIS file imported "
                                  "into a new, empty EndNote library.")
    rec_start = st.number_input("First record number", min_value=1, value=1, step=1,
                                help="Leave at 1 for a new library.")
    remove_list = st.checkbox("Remove the old reference list from the Word copy", value=True)

st.title("RefCheck")
st.caption("Extract references, verify each one in PubMed, and prepare an EndNote library "
           "plus a Word copy whose citations EndNote can take over.")

# ------------------------------------------------------------- step 1 -----
st.subheader("1. Upload")
up = st.file_uploader("Manuscript or reference list",
                      type=["docx", "doc", "odt", "rtf", "pdf", "txt", "md", "csv", "tsv", "xlsx"])
if up is None:
    st.info("Word gives the best result: citations in the text can then be converted for EndNote.")
    st.stop()

sig = hashlib.md5(up.getvalue()).hexdigest() + "|" + heading
if ss.get("sig") != sig:
    for k in ("extraction", "refs_df", "results", "outputs", "match_sig"):
        ss.pop(k, None)
    ss.sig = sig
    ss.workdir = tempfile.mkdtemp(prefix="refcheck_")
    src = Path(ss.workdir) / up.name
    src.write_bytes(up.getvalue())
    try:
        ss.extraction = extract_references(src, heading or None)
    except Exception as e:
        st.error(str(e))
        st.stop()
    ss.refs_df = pd.DataFrame(ss.extraction["refs"])[EDIT_COLS]
    ss.forms, ss.suggest = None, dict(brackets=True, superscript=True, parentheses=False, glued=False)
    if ss.extraction.get("docx_path") and ss.extraction.get("heading_para") is not None:
        try:
            ss.forms = detect_citation_forms(ss.extraction["docx_path"], ss.extraction["heading_para"],
                                             len(ss.extraction["refs"]))
            ss.suggest = suggested_options(ss.forms)
        except Exception:
            pass

ex = ss.extraction
for w in ex["warnings"]:
    st.warning(w)
st.success(f"Found **{len(ss.refs_df)}** references in {up.name}.")

with st.expander("Parsed references (edit any cell to fix a parsing mistake before searching)",
                 expanded=False):
    edited = st.data_editor(
        ss.refs_df, hide_index=True, width="stretch", num_rows="fixed",
        disabled=["num"], key="ref_editor",
        column_config={"text": st.column_config.TextColumn("original reference", width="large"),
                       "title": st.column_config.TextColumn(width="large"),
                       "pmid_hint": st.column_config.TextColumn("PMID in text")})

refs = []
for rec, row in zip(ex["refs"], edited.fillna("").astype(str).to_dict("records")):
    r = dict(rec)
    r.update({k: row[k] for k in EDIT_COLS if k != "num"})
    refs.append(r)
match_sig = hashlib.md5(edited.to_csv().encode()).hexdigest()

# ------------------------------------------------------------- step 2 -----
st.subheader("2. Check against PubMed")
stale = ss.get("results") is not None and ss.get("match_sig") != match_sig
if stale:
    st.warning("References were edited after the last search. Run the check again.")
go = st.button("Check all references in PubMed", type="primary",
               disabled=not email or "@" not in email)
if not email or "@" not in email:
    st.caption("Enter your email in the sidebar first.")

if go:
    log_lines = []
    client = PubMedClient(email, api_key, log=log_lines.append)
    bar = st.progress(0.0, text="Starting")

    def prog(i, n, ref, res):
        best = res.get("best")
        bar.progress(i / n, text=f"{i}/{n}  ref {ref['num']}: {res['status']}"
                                 + (f"  (PMID {best['pmid']})" if best else ""))

    ss.results = run_matching(refs, client, prog)
    ss.match_sig = match_sig
    ss.search_log = log_lines
    ss.pop("outputs", None)
    ss.pop("review_sig", None)
    bar.empty()

if ss.get("results") is None:
    st.stop()
results = ss.results

# ------------------------------------------------------------- step 3 -----
st.subheader("3. Review the matches")
counts = pd.Series([r["status"] for r in results.values()]).value_counts()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Matched", int(counts.get("AUTO_MATCH", 0)))
c2.metric("Need a look", int(counts.get("REVIEW", 0)))
c3.metric("Not in PubMed", int(counts.get("NO_MATCH", 0)))
c4.metric("Errors", int(counts.get("ERROR", 0)))

ref_by_num = {r["num"]: r for r in refs}


def base_review_df():
    rows = []
    for ref in refs:
        res = results[ref["num"]]
        b = res.get("best")
        rows.append({
            "ref": ref["num"], "status": STATUS_ICON.get(res["status"], res["status"]),
            "checked": False,
            "use PMID": b["pmid"] if (b and res["status"] == "AUTO_MATCH") else "",
            "suggested": b["pmid"] if b else "",
            "title match": round(100 * b["title_similarity"]) if b else None,
            "your title": ref["title"],
            "PubMed title": b["title"] if b else "",
            "PubMed": f"https://pubmed.ncbi.nlm.nih.gov/{b['pmid']}/" if b else None,
            "why": b["match_reasons"] if b else res.get("note", ""),
        })
    return pd.DataFrame(rows)


if ss.get("review_sig") != match_sig:
    ss.review_df, ss.review_sig, ss.rev_ver = base_review_df(), match_sig, 0


def set_row(num, **vals):
    df = ss.review_df.copy()
    for k, v in vals.items():
        df.loc[df["ref"] == num, k] = v
    ss.review_df = df
    ss.rev_ver += 1            # refresh the table so it shows the change


def typed_pmid(num):
    val = "".join(ch for ch in ss.get(f"oth{num}", "") if ch.isdigit())
    if val:
        set_row(num, **{"use PMID": val, "checked": True})
    ss[f"oth{num}"] = ""


n_checked = int(ss.review_df["checked"].sum())
n_use = int((ss.review_df["use PMID"].fillna("").astype(str) != "").sum())
st.caption(f"{n_use} of {len(refs)} references currently go to EndNote with a PMID. "
           f"{n_checked} marked as checked by you. Rows without a PMID are imported as typed in "
           f"your document and labelled unmatched in EndNote.")

tab_one, tab_table = st.tabs(["Compare one by one", "All references (table)"])

# ---- one by one comparison ------------------------------------------------
with tab_one:
    flt = st.radio("Go through", ["All", "Matched", "Need a look", "Not in PubMed", "Not yet checked"],
                   horizontal=True, key="flt")
    df = ss.review_df
    pick = {"All": df, "Matched": df[df["status"].str.contains("matched")],
            "Need a look": df[df["status"].str.contains("check")],
            "Not in PubMed": df[df["status"].str.contains("no match|error", regex=True)],
            "Not yet checked": df[~df["checked"]]}[flt]
    nums = pick["ref"].tolist()
    if not nums:
        st.info("Nothing in this group.")
    else:
        pos = ss.get("pos", 0) % len(nums)
        nav1, nav2, nav3 = st.columns([1, 4, 1])
        if nav1.button("◀ Previous", width="stretch"):
            pos = (pos - 1) % len(nums)
        if nav3.button("Next ▶", width="stretch"):
            pos = (pos + 1) % len(nums)
        num = nav2.selectbox("Reference", nums, index=pos, label_visibility="collapsed",
                             format_func=lambda n: f"Ref {n}  ·  {ref_by_num[n]['title'][:90]}")
        ss.pos = nums.index(num)

        ref, res = ref_by_num[num], results[num]
        row = ss.review_df[ss.review_df["ref"] == num].iloc[0]
        cands = [c for c in (res.get("best"), res.get("second")) if c]
        chosen = str(row["use PMID"] or "")
        st.markdown(f"**Status:** {row['status']}   ·   **Going to EndNote with:** "
                    + (f"PMID [{chosen}](https://pubmed.ncbi.nlm.nih.gov/{chosen}/)" if chosen
                       else "no PMID (as typed in your document)")
                    + ("   ·   ✅ checked" if row["checked"] else ""))

        if chosen and chosen not in [c["pmid"] for c in cands]:
            extra = ss.setdefault("extra_records", {})
            if chosen not in extra:
                try:
                    got = PubMedClient(email, api_key).efetch([chosen])
                    extra[chosen] = got[0] if got else None
                except Exception as e:
                    extra[chosen] = {"error": str(e)}
            rec = extra[chosen]
            with st.container(border=True):
                if not rec:
                    st.error(f"PMID {chosen} was not found in PubMed. Check the number.")
                elif "error" in rec:
                    st.warning(f"Could not fetch PMID {chosen} to show it: {rec['error']}")
                else:
                    st.markdown(f"**The PMID you chose:** [PMID {chosen}](https://pubmed.ncbi.nlm.nih.gov/{chosen}/)")
                    st.dataframe(pd.DataFrame(compare_fields(ref, rec),
                                              columns=["", "your document", "PubMed", "same?"]),
                                 hide_index=True, width="stretch")
        if not cands:
            st.write("**Your reference**")
            st.info(ref["text"])
            st.write("No PubMed record came close. This is expected for preprints, books, reports, "
                     "software and web pages. If you know the PMID, enter it below.")
        for i, cand in enumerate(cands):
            label = "Best PubMed match" if i == 0 else "Runner up"
            with st.container(border=True):
                st.markdown(f"**{label}:** [PMID {cand['pmid']}](https://pubmed.ncbi.nlm.nih.gov/{cand['pmid']}/)"
                            f"  ·  score {cand['score']}  ·  title match {round(100 * cand['title_similarity'])}%")
                cmp = compare_fields(ref, cand)
                st.dataframe(pd.DataFrame(cmp, columns=["", "your document", "PubMed", "same?"]),
                             hide_index=True, width="stretch")
                if cand.get("abstract"):
                    with st.expander("PubMed abstract"):
                        st.write(cand["abstract"])
                b1, b2 = st.columns(2)
                if b1.button(f"✔ Use PMID {cand['pmid']}", key=f"use{num}_{i}", width="stretch",
                             type="primary" if i == 0 else "secondary"):
                    set_row(num, **{"use PMID": cand["pmid"], "checked": True})
                    st.rerun()
        a1, a2, a3 = st.columns([2, 2, 3])
        if a1.button("✖ Not in PubMed (no PMID)", key=f"none{num}", width="stretch"):
            set_row(num, **{"use PMID": "", "checked": True})
            st.rerun()
        if a2.button("Mark as checked", key=f"chk{num}", width="stretch"):
            set_row(num, checked=True)
            st.rerun()
        a3.text_input("Or type the correct PMID and press Enter", key=f"oth{num}",
                      placeholder="e.g. 35357562", on_change=typed_pmid, args=(num,))

# ---- table ----------------------------------------------------------------
with tab_table:
    t1, t2 = st.columns([1, 3])
    if t1.button("Accept all yellow suggestions"):
        df = ss.review_df.copy()
        mask = df["status"].str.contains("check")
        df.loc[mask, "use PMID"] = df.loc[mask, "suggested"]
        ss.review_df, ss.rev_ver = df, ss.rev_ver + 1
    t2.caption("Edit **use PMID** directly, or tick **checked** once you have looked at a row.")
    edited_review = st.data_editor(
        ss.review_df, hide_index=True, width="stretch", key=f"review_tbl_{ss.rev_ver}",
        disabled=[c for c in ss.review_df.columns if c not in ("use PMID", "checked")],
        column_config={
            "checked": st.column_config.CheckboxColumn(width="small"),
            "use PMID": st.column_config.TextColumn(width="small"),
            "PubMed": st.column_config.LinkColumn(display_text="open"),
            "PubMed title": st.column_config.TextColumn(width="large"),
            "your title": st.column_config.TextColumn(width="large"),
            "title match": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%"),
        })
    if not edited_review[["use PMID", "checked"]].equals(ss.review_df[["use PMID", "checked"]]):
        ss.review_df = edited_review.copy()

with st.expander("Search log"):
    st.code("\n".join(ss.get("search_log", [])[-400:]) or "(empty)")

# ------------------------------------------------------------- step 4 -----
is_word = bool(ex.get("docx_path")) and bool(ex.get("heading_para"))
st.subheader("4. Download" if not is_word else "4. Build EndNote files and the Word copy")
if not is_word:
    st.caption("Your file is not a Word manuscript, so you get the EndNote import file and the "
               "report. Upload the .docx version of the manuscript if you also want its citations "
               "converted for EndNote.")
opt_brackets = opt_super = opt_paren = opt_glued = False
if ss.get("forms") is not None:
    f, sg, k = ss.forms, ss.suggest, ss.sig[:8]
    st.markdown("**Citations in your Word text to convert** (preset from what was found in the document)")
    c1, c2, c3, c4 = st.columns(4)
    opt_brackets = c1.checkbox(f"Brackets [1,2]  ({f['brackets']} found)", value=sg["brackets"], key="ob" + k)
    opt_super = c2.checkbox(f"Superscript  ({f['superscript']} found)", value=sg["superscript"], key="os" + k)
    opt_paren = c3.checkbox(f"Parentheses (1,2)  ({f['parentheses']} found)", value=sg["parentheses"],
                            key="op" + k, help="'(1)' is also used for list items, so check the report.")
    opt_glued = c4.checkbox(f"Attached to words: text1,2  ({f['glued']} found)", value=sg["glued"],
                            key="og" + k,
                            help="For citations that lost their superscript formatting. Gene and "
                                 "genome names such as IDH1, TP53, hg19 and chr17 are never touched.")
if st.button("Build files", type="primary"):
    overrides = dict(zip(ss.review_df["ref"], ss.review_df["use PMID"].fillna("").astype(str)))
    reviewed = dict(zip(ss.review_df["ref"], ss.review_df["checked"]))
    decisions = apply_overrides(default_decisions(refs, results), results, overrides)
    client = PubMedClient(email, api_key)
    stem = Path(up.name).stem
    with st.spinner("Fetching records and writing files"):
        ss.outputs = build_outputs(
            ex, refs, results, decisions, client, Path(ss.workdir) / "out", stem,
            record_start=int(rec_start), annotate=is_word, remove_reference_list=remove_list,
            reviewed=reviewed,
            annotate_opts=dict(brackets=opt_brackets, superscript=opt_super,
                               parentheses=opt_paren, glued=opt_glued,
                               use_record_numbers=use_recnum))

out = ss.get("outputs")
if out:
    for w in out["warnings"]:
        st.warning(w)
    p = out["paths"]
    st.success(f"{out['n_records']} EndNote records written for {len(refs)} references.")
    files = [("EndNote import (.ris)", "ris", "application/x-research-info-systems"),
             ("Word copy for EndNote", "docx",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
             ("Report (.xlsx)", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
             ("Report (.csv)", "csv", "text/csv"), ("PMID list (.txt)", "pmids", "text/plain"),
             ("Everything (.zip)", "zip", "application/zip")]
    files = [f for f in files if f[1] in p]
    for col, (label, key, mime) in zip(st.columns(len(files)), files):
        if key in p:
            col.download_button(label, Path(p[key]).read_bytes(), file_name=Path(p[key]).name,
                                mime=mime, width="stretch")
    if out["citation_log"]:
        conv = sum(1 for e in out["citation_log"] if e["replaced_with"])
        st.caption(f"In-text citations converted: {conv}. "
                   f"Left unchanged: {len(out['citation_log']) - conv} (listed in the report).")

    step_word = (f"""
3. **Word:** open the *Word copy*, choose your output style in the EndNote tab, then click
   **Update Citations and Bibliography**. Every `{{Author, Year #Record}}` becomes a live EndNote
   citation and the bibliography is rebuilt at the end of the document.""" if "docx" in p else "")
    with st.expander("How to finish in EndNote", expanded=True):
        st.markdown(f"""
1. **EndNote:** File > New to create an **empty** library. Then File > Import > File, choose
   `{Path(p['ris']).name}`, Import Option **Reference Manager (RIS)**, Text Translation **Unicode (UTF-8)**.
2. **Check:** show the *Record Number* and *Label* columns. Label holds your reference number.
   Record {int(rec_start)} should have Label 1, record {int(rec_start) + 1} Label 2, and so on.""" +
            (" If they do not line up, set *First record number* in the sidebar and rebuild." if "docx" in p else "")
            + step_word + """
4. In EndNote, search Keywords for `refcheck NO_MATCH` to find references that are not in PubMed.

The report lists every reference with its PMID, link, score, the fields from your document next to
the PubMed fields, and whether you checked it.
""")
