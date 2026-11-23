"""RefCheck: verify a manuscript's references against PubMed, audit its citations, and
prepare an EndNote library plus a Word copy whose citations EndNote can take over.

Run:   streamlit run app.py

Privacy: the uploaded file is kept in this session's memory. It is written to disk only
inside refcheck.privacy.workspace(), which deletes the folder when the step ends.
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from refcheck import __version__
from refcheck.annotate import detect_citation_forms, suggested_options
from refcheck.config import load_settings
from refcheck.extract import extract_references
from refcheck.netaudit import DiskCache, NetworkAudit
from refcheck.pipeline import apply_overrides, build_outputs, default_decisions, run_matching
from refcheck.privacy import NEVER_SENT, TRANSMITTED, detect_deployment, safe_stem, workspace
from refcheck.pubmed import PubMedClient
from refcheck.report import write_html, write_json
from refcheck.review import compare_fields
from refcheck.roundtrip import validate_roundtrip
from refcheck.sources import evidence_from_settings
from refcheck.verify import STATUS_HELP, classify_all

st.set_page_config(page_title="RefCheck", page_icon="📚", layout="wide")
ss = st.session_state
EDIT_COLS = ["num", "authors", "title", "journal", "year", "volume", "issue", "pages", "doi", "pmid_hint", "text"]
STATUS_ICON = {"AUTO_MATCH": "🟢 matched", "REVIEW": "🟡 check", "NO_MATCH": "🔴 no match",
               "ERROR": "⚠️ error", "MANUAL": "🔵 manual"}
INTEGRITY_ICON = {"VERIFIED": "✅", "VERIFIED_WITH_WARNINGS": "☑️", "METADATA_ERROR": "🟠", "DOI_MISMATCH": "⛔",
                  "PMID_MISMATCH": "⛔", "DUPLICATE": "♊", "RETRACTED": "🚫", "EXPRESSION_OF_CONCERN": "⚠️",
                  "CORRECTED": "📝", "PREPRINT": "📄", "NOT_FOUND_IN_PUBMED": "❔", "NON_PUBMED_SOURCE": "📚",
                  "UNRESOLVED": "🟡", "CHECK_ERROR": "⚠️"}

settings = load_settings()
try:
    host = st.context.headers.get("Host", "")
except Exception:
    host = ""
deployment = detect_deployment(settings.deployment, host)
if deployment == "hosted":
    settings.cache_path = ""          # no shared server side cache of other people's searches
if "audit" not in ss:
    ss.audit = NetworkAudit()


SOURCE_CHOICES = [
    ("crossref", "Crossref: DOI, retractions (incl. Retraction Watch), preprints",
     "DOI; for unmatched references title, first author, journal and year"),
    ("openalex", "OpenAlex: existence outside PubMed, retraction flag", "DOI; for unmatched references the title"),
    ("doi", "doi.org: is the DOI registered", "DOI"),
    ("nlmcatalog", "NLM Catalog: journal known and indexed", "journal abbreviation or ISSN"),
    ("doaj", "DOAJ: open access journal listing", "ISSN"),
    ("unpaywall", "Unpaywall: legal open access copy", "DOI and your email"),
    ("retractionwatch", "Retraction Watch dataset: checked locally, updated from GitLab when a new version "
     "is published", "nothing from the manuscript (a file checksum request, and the file download when it changed)"),
]


def make_evidence(client):
    try:
        ev = evidence_from_settings(settings, client, email)
        if ev is not None and ev.rw_status is not None:
            (st.caption if ev.rw_status.usable else st.warning)(ev.rw_status.line())
        return ev
    except OSError as e:
        st.warning(f"Retraction Watch file could not be read ({type(e).__name__}); continuing without it.")
        settings.retraction_watch_csv = ""
        return evidence_from_settings(settings, client, email)


def make_client(log=None):
    c = PubMedClient(email, api_key, log=log, settings=settings)
    c.audit = ss.audit                  # one audit for the whole session
    return c


def ilabel(status):
    return f"{INTEGRITY_ICON.get(status, '')} {status}"


# ------------------------------------------------------------- sidebar ----
with st.sidebar:
    if deployment == "hosted":
        st.warning("**Hosted copy.** Files you upload are sent to and processed on this server. "
                   "For unpublished manuscripts, install RefCheck and run it on your own computer "
                   "(see docs/INSTALLATION.md).", icon="🌐")
    else:
        st.success("**Running on this computer.** Your file stays here. Only bibliographic search "
                   "terms are sent to PubMed.", icon="🔒")
    st.header("Settings")
    email = st.text_input("Your email (NCBI requires one)", value=ss.get("email", settings.ncbi_email))
    ss.email = email
    api_key = st.text_input("NCBI API key (optional, 3x faster)", value=settings.ncbi_api_key, type="password")
    heading = st.text_input("Reference section heading", value="", placeholder="References (auto)",
                            help="Only needed if your section is called something unusual.")
    st.subheader("EndNote")
    use_recnum = st.checkbox("Include EndNote record numbers", value=True,
                             help="Gives an exact link to each record. Needs the RIS file imported into a "
                                  "new, empty EndNote library, because EndNote numbers records itself.")
    rec_start = st.number_input("First record number", min_value=1, value=1, step=1,
                                help="Leave at 1 for a new library.")
    remove_list = st.checkbox("Remove the old reference list from the Word copy", value=True)

    with st.expander("Extra checks after PubMed"):
        st.caption("Each source receives only the identifiers or citation fields listed. Untick a source "
                   "to keep its checks off. A source that cannot be reached is reported as not checked.")
        picked = []
        for key, label, what in SOURCE_CHOICES:
            if st.checkbox(label, value=key in settings.sources, key=f"src_{key}", help=f"Sends: {what}"):
                picked.append(key)
        settings.sources = picked
        if deployment == "local":
            rw_path = st.text_input("Your own Retraction Watch CSV (optional, used as is)",
                                    value=settings.retraction_watch_csv,
                                    help="Path to retraction_watch.csv from your own clone of "
                                         "gitlab.com/crossref/retraction-watch-data. Leave empty to let RefCheck "
                                         "keep its own copy up to date.")
            settings.retraction_watch_csv = rw_path.strip()

    with st.expander("Privacy and network"):
        st.markdown("**Sent over HTTPS, by destination**")
        st.markdown("\n".join(f"- *{req}* ({dest}): {content}" for req, dest, content in TRANSMITTED))
        st.markdown(f"**Never sent:** {NEVER_SENT}")
        s = ss.audit.summary()
        st.markdown(f"**This session:** {s['network_requests']} requests "
                    f"({', '.join(f'{h} {n}' for h, n in s['by_destination'].items()) or 'none'}), "
                    f"{s['answered_from_cache']} answered from the local cache, {s['errors']} failed.")
        if ss.audit.requests and st.checkbox("Show every request", key="show_req"):
            st.dataframe(pd.DataFrame([dict(time=r.time, host=r.host, endpoint=r.endpoint, cached=r.from_cache,
                                            status=r.status, sent="; ".join(f"{k}={v}" for k, v in r.transmitted.items()
                                                                            if k not in ("db", "retmode", "tool")))
                                       for r in ss.audit.requests]), hide_index=True, width="stretch")
        if deployment == "local":
            offline = st.checkbox("Offline: answer only from the local cache", value=settings.offline,
                                  help="No network requests. References not looked up before stay unresolved.")
            settings.offline = offline
            if settings.cache_path:
                try:
                    cs = DiskCache(settings.cache_path).stats()
                    st.caption(f"Local PubMed cache: {cs['entries']} answers, {cs['size_kb']} kB at {cs['path']}")
                    if st.button("Clear the local PubMed cache"):
                        DiskCache(settings.cache_path).clear()
                        st.rerun()
                except Exception:
                    st.caption("Local PubMed cache unavailable.")
        else:
            st.caption("On the hosted copy nothing is cached on disk and the session's data is held in "
                       "memory until you delete it or close the tab.")
        if st.button("Delete my session data", help="Removes your file, results and downloads from this session."):
            for k in list(ss.keys()):
                del ss[k]
            st.rerun()
    st.caption(f"RefCheck {__version__}")

st.title("RefCheck")
st.caption("Extract references, verify each one in PubMed, audit the citations in your text, and prepare "
           "an EndNote library plus a Word copy whose citations EndNote can take over.")

# ------------------------------------------------------------- step 1 -----
st.subheader("1. Upload")
up = st.file_uploader("Manuscript or reference list",
                      type=["docx", "doc", "odt", "rtf", "pdf", "txt", "md", "csv", "tsv", "xlsx",
                            "bib", "bbl", "tex"])
if up is None:
    st.info("Word gives the best result: citations in the text can then be audited and converted for EndNote.")
    st.stop()

data = up.getvalue()
sig = hashlib.sha256(data).hexdigest() + "|" + heading
if ss.get("sig") != sig:
    for k in ("extraction", "refs_df", "results", "outputs", "out_files", "match_sig", "review_sig",
              "docx_bytes", "roundtrip", "history", "extra_records"):
        ss.pop(k, None)
    ss.sig = sig
    ss.input_sha = hashlib.sha256(data).hexdigest()
    ss.input_name = safe_stem(up.name) + Path(up.name).suffix.lower()
    ss.started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ss.forms, ss.suggest = None, dict(brackets=True, superscript=True, parentheses=False, glued=False)
    try:
        with workspace({ss.input_name: data}) as d:
            ex = extract_references(d / ss.input_name, heading or None, workdir=d)
            if ex.get("docx_path"):
                ss.docx_bytes = Path(ex["docx_path"]).read_bytes()
                if ex.get("heading_para"):
                    try:
                        ss.forms = detect_citation_forms(ex["docx_path"], ex["heading_para"], len(ex["refs"]))
                        ss.suggest = suggested_options(ss.forms)
                    except Exception as e:
                        ex["warnings"].append(f"Could not scan the text for citation styles ({type(e).__name__}); "
                                              "the citation options use their defaults.")
            ex["has_docx"], ex["docx_path"] = bool(ex.get("docx_path")), None
    except Exception as e:
        st.error(f"Could not read the file: {e}")
        st.stop()
    ss.extraction = ex
    ss.refs_df = pd.DataFrame(ex["refs"])[EDIT_COLS]

ex = ss.extraction
for w in ex["warnings"]:
    st.warning(w)
st.success(f"Found **{len(ss.refs_df)}** references in {ss.input_name}.")


def parse_flag(rec):
    low = [k for k, v in (rec.get("confidence") or {}).items() if v == "low"]
    miss = [k for k in ("title", "journal", "year") if (rec.get("confidence") or {}).get(k) == "missing"]
    if rec.get("parse_note"):
        return "⚠ " + rec["parse_note"]
    if low or miss:
        return "⚠ " + ", ".join([f"unsure: {k}" for k in low] + [f"missing: {k}" for k in miss])
    return "ok"


n_unsure = sum(1 for r in ex["refs"] if parse_flag(r) != "ok")
with st.expander(f"Parsed references ({n_unsure} with unsure or missing fields). Edit any cell to fix a "
                 "parsing mistake before searching.", expanded=n_unsure > 0 and n_unsure <= 10):
    view = ss.refs_df.copy()
    view.insert(1, "parse check", [parse_flag(r) for r in ex["refs"]])
    edited = st.data_editor(
        view, hide_index=True, width="stretch", num_rows="fixed", disabled=["num", "parse check"],
        key="ref_editor",
        column_config={"text": st.column_config.TextColumn("original reference", width="large"),
                       "title": st.column_config.TextColumn(width="large"),
                       "pmid_hint": st.column_config.TextColumn("PMID in text")})
    edited = edited.drop(columns=["parse check"])

refs = []
for rec, row in zip(ex["refs"], edited.fillna("").astype(str).to_dict("records")):
    r = dict(rec)
    r.update({k: row[k] for k in EDIT_COLS if k != "num"})
    refs.append(r)
match_sig = hashlib.sha256(edited.to_csv().encode()).hexdigest()

# ------------------------------------------------------------- step 2 -----
st.subheader("2. Check against PubMed")
if ss.get("results") is not None and ss.get("match_sig") != match_sig:
    st.warning("References were edited after the last search. Run the check again.")
go = st.button("Check all references in PubMed", type="primary", disabled=not email or "@" not in email)
if not email or "@" not in email:
    st.caption("Enter your email in the sidebar first.")
st.caption("Only search terms built from each parsed reference are sent. See *Privacy and network* in the sidebar.")

if go:
    log_lines = []
    client = make_client(log=log_lines.append)
    bar = st.progress(0.0, text="Starting")

    def prog(i, n, ref, res):
        best = res.get("best")
        bar.progress(i / n, text=f"{i}/{n}  ref {ref['num']}: {res['status']}"
                                 + (f"  (PMID {best['pmid']})" if best else ""))

    ss.results = run_matching(refs, client, prog)
    ss.match_sig = match_sig
    ss.search_log = log_lines
    for k in ("outputs", "out_files", "review_sig", "roundtrip"):
        ss.pop(k, None)
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
if any(r.get("error_kinds") for r in results.values()):
    kinds = sorted({k for r in results.values() for k in r.get("error_kinds") or []})
    st.warning("Some searches failed (" + ", ".join(kinds) + "). Those references are not judged; run the "
               "check again later or add an NCBI API key.")

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
            "why": ("; ".join(res.get("abstain") or []) or b["match_reasons"]) if b else res.get("note", ""),
        })
    return pd.DataFrame(rows)


if ss.get("review_sig") != match_sig:
    ss.review_df, ss.review_sig, ss.rev_ver, ss.history = base_review_df(), match_sig, 0, []


def remember():
    ss.history = (ss.get("history") or [])[-49:] + [ss.review_df.copy()]


def set_row(num, **vals):
    remember()
    df = ss.review_df.copy()
    for k, v in vals.items():
        df.loc[df["ref"] == num, k] = v
    ss.review_df = df
    ss.rev_ver += 1


def typed_pmid(num):
    val = "".join(ch for ch in ss.get(f"oth{num}", "") if ch.isdigit())
    if val:
        set_row(num, **{"use PMID": val, "checked": True})
    ss[f"oth{num}"] = ""


def current_decisions():
    overrides = dict(zip(ss.review_df["ref"], ss.review_df["use PMID"].fillna("").astype(str)))
    return apply_overrides(default_decisions(refs, results), results, overrides)


def integrity_preview():
    recs = {c["pmid"]: c for r in results.values() for c in (r.get("best"), r.get("second")) if c}
    recs.update({k: v for k, v in (ss.get("extra_records") or {}).items() if v and "error" not in v})
    reviewed = dict(zip(ss.review_df["ref"], ss.review_df["checked"]))
    return classify_all(refs, results, current_decisions(), recs, reviewed)


integ = integrity_preview()
n_checked = int(ss.review_df["checked"].sum())
n_use = int((ss.review_df["use PMID"].fillna("").astype(str) != "").sum())
ic = pd.Series([v["status"] for v in integ.values()]).value_counts()
st.caption(f"{n_use} of {len(refs)} references currently go to EndNote with a PMID. {n_checked} marked as "
           f"checked by you. Integrity so far: " + ", ".join(f"{ilabel(k)} {v}" for k, v in ic.items()))
st.caption("The match score is an evidence score, not a probability. 'Not in PubMed' does not mean a "
           "reference is fabricated.")
u1, u2 = st.columns([1, 5])
if u1.button("↶ Undo last decision", disabled=not ss.get("history")):
    ss.review_df = ss.history.pop()
    ss.rev_ver += 1
    st.rerun()

tab_one, tab_table = st.tabs(["Compare one by one", "All references (table)"])

# ---- one by one comparison ------------------------------------------------
with tab_one:
    flt = st.radio("Go through", ["All", "Matched", "Need a look", "Not in PubMed", "Not yet checked",
                                  "Integrity problems"], horizontal=True, key="flt")
    df = ss.review_df
    problem_refs = [n for n, v in integ.items() if v["status"] not in ("VERIFIED", "VERIFIED_WITH_WARNINGS")]
    pick = {"All": df, "Matched": df[df["status"].str.contains("matched")],
            "Need a look": df[df["status"].str.contains("check")],
            "Not in PubMed": df[df["status"].str.contains("no match|error", regex=True)],
            "Not yet checked": df[~df["checked"]],
            "Integrity problems": df[df["ref"].isin(problem_refs)]}[flt]
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
        it = integ[num]
        st.markdown(f"**Integrity:** {ilabel(it['status'])} · {it['help']}  \n*decided by: {it['decided_by']}*")
        for f in it["findings"]:
            st.markdown(f"- `{f['code']}` {f['detail']}  \n  <small>source: {f['source']}</small>",
                        unsafe_allow_html=False)
        if res.get("abstain"):
            st.caption("RefCheck did not accept the best candidate automatically because: "
                       + "; ".join(res["abstain"]))

        if chosen and chosen not in [c["pmid"] for c in cands]:
            extra = ss.setdefault("extra_records", {})
            if chosen not in extra:
                try:
                    got = make_client().efetch([chosen])
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
                notes = [t for t in (cand.get("pubtypes") or []) if t in ("Retracted Publication", "Preprint",
                                                                         "Retraction of Publication")]
                if notes or (cand.get("links") or {}).get("retracted_in"):
                    st.error("PubMed marks this record: " + ", ".join(notes or ["retracted"]))
                st.dataframe(pd.DataFrame(compare_fields(ref, cand), columns=["", "your document", "PubMed", "same?"]),
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
    t1, t2, t3 = st.columns([1, 2, 2])
    if t1.button("Accept all yellow suggestions"):
        remember()
        df = ss.review_df.copy()
        mask = df["status"].str.contains("check")
        df.loc[mask, "use PMID"] = df.loc[mask, "suggested"]
        ss.review_df, ss.rev_ver = df, ss.rev_ver + 1
        st.rerun()
    q = t2.text_input("Search", placeholder="title, PMID or reference number", key="tbl_q")
    ist = t3.multiselect("Integrity status", sorted({v["status"] for v in integ.values()}), key="tbl_ist")
    shown = ss.review_df.copy()
    shown.insert(1, "integrity", [ilabel(integ[n]["status"]) for n in shown["ref"]])
    if q:
        ql = q.lower()
        shown = shown[shown.apply(lambda r: ql in " ".join(map(str, r.values)).lower(), axis=1)]
    if ist:
        shown = shown[shown["ref"].map(lambda n: integ[n]["status"] in ist)]
    st.caption("Edit **use PMID** directly, or tick **checked** once you have looked at a row. Click a column "
               "header to sort.")
    edited_review = st.data_editor(
        shown, hide_index=True, width="stretch", key=f"review_tbl_{ss.rev_ver}",
        disabled=[c for c in shown.columns if c not in ("use PMID", "checked")],
        column_config={
            "checked": st.column_config.CheckboxColumn(width="small"),
            "use PMID": st.column_config.TextColumn(width="small"),
            "PubMed": st.column_config.LinkColumn(display_text="open"),
            "PubMed title": st.column_config.TextColumn(width="large"),
            "your title": st.column_config.TextColumn(width="large"),
            "title match": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%"),
        })
    before = ss.review_df.set_index("ref").loc[edited_review["ref"], ["use PMID", "checked"]]
    after = edited_review.set_index("ref")[["use PMID", "checked"]]
    if not after.astype(str).equals(before.astype(str)):
        remember()
        df = ss.review_df.set_index("ref")
        df.loc[after.index, ["use PMID", "checked"]] = after
        ss.review_df = df.reset_index()

with st.expander("Search log"):
    st.code("\n".join(ss.get("search_log", [])[-400:]) or "(empty)")

# ------------------------------------------------------------- step 4 -----
is_word = bool(ex.get("has_docx")) and bool(ex.get("heading_para"))
st.subheader("4. Download" if not is_word else "4. Build EndNote files and the Word copy")
if not is_word:
    st.caption("Your file is not a Word manuscript, so you get the EndNote import files and the reports. "
               "Upload the .docx version of the manuscript if you also want its citations audited and "
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
                            help="For citations that lost their superscript formatting. Gene and genome names "
                                 "such as IDH1, TP53, hg19 and chr17 are never touched.")

MIME = {".ris": "application/x-research-info-systems", ".xml": "application/xml",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".csv": "text/csv",
        ".txt": "text/plain", ".bib": "application/x-bibtex", ".zip": "application/zip", ".json": "application/json", ".html": "text/html"}
LABELS = [("EndNote import (.ris)", "ris"), ("Word copy for EndNote", "docx"),
          ("Integrity report (.html)", "html"), ("Report (.xlsx)", "xlsx"), ("Report (.csv)", "csv"),
          ("Integrity report (.json)", "json"), ("EndNote XML (.xml)", "endnote_xml"),
          ("BibTeX (.bib)", "bib"), ("PMID list (.txt)", "pmids"), ("Everything (.zip)", "zip")]

if st.button("Build files", type="primary"):
    overrides = dict(zip(ss.review_df["ref"], ss.review_df["use PMID"].fillna("").astype(str)))
    reviewed = dict(zip(ss.review_df["ref"], ss.review_df["checked"]))
    decisions = apply_overrides(default_decisions(refs, results), results, overrides)
    stem = safe_stem(ss.input_name)
    files = {"manuscript.docx": ss.docx_bytes} if ss.get("docx_bytes") else {}
    with st.spinner("Fetching records and writing files"):
        with workspace(files) as d:
            ex2 = dict(ex, docx_path=str(d / "manuscript.docx") if files else None)
            cl = make_client()
            out = build_outputs(
                ex2, refs, results, decisions, cl, d / "out", stem, evidence=make_evidence(cl),
                record_start=int(rec_start), annotate=is_word, remove_reference_list=remove_list,
                reviewed=reviewed,
                report_meta=dict(input_name=ss.input_name, input_sha256=ss.input_sha, started=ss.started,
                                 overrides=overrides, deployment=deployment),
                annotate_opts=dict(brackets=opt_brackets, superscript=opt_super, parentheses=opt_paren,
                                   glued=opt_glued, use_record_numbers=use_recnum))
            ss.out_files = {k: (Path(p).name, Path(p).read_bytes()) for k, p in out["paths"].items()
                            if Path(p).exists()}
    out.pop("paths")
    ss.outputs, ss.overrides, ss.reviewed = out, overrides, reviewed
    ss.pop("roundtrip", None)

out = ss.get("outputs")
if out:
    for w in out["warnings"]:
        st.warning(w)
    st.success(f"{out['n_records']} EndNote records written for {len(refs)} references.")
    rs = out.get("reference_style") or {}
    if rs.get("family"):
        st.caption(f"Citation style detected: {rs['family_label']}"
                   + (f", best guess {rs['style_guess']}" if rs.get("style_guess") else "")
                   + f" ({rs['detected']} of {rs['references']} references recognised). "
                   "Detection is for information; several journal styles are identical for one reference.")
    m = st.columns(min(6, max(1, len(out["integrity_summary"]))))
    for col, (k, v) in zip(m, out["integrity_summary"].items()):
        col.metric(ilabel(k), v, help=STATUS_HELP.get(k))
    if out.get("audit_findings"):
        st.markdown("**Citation audit**")
        for line in out["audit_findings"]:
            st.markdown(f"- {line}")
    ch = out.get("changes")
    if ch:
        st.caption(f"Word copy: {ch['n_changed']} citations converted, {ch['n_unchanged']} left unchanged "
                   "(listed in the reports). Structure check: "
                   + ("document structure preserved." if ch["structure_preserved"] else "see warnings."))
    files = [(label, key) for label, key in LABELS if key in ss.out_files]
    for row_start in range(0, len(files), 5):
        cols = st.columns(5)
        for col, (label, key) in zip(cols, files[row_start:row_start + 5]):
            name, blob = ss.out_files[key]
            col.download_button(label, blob, file_name=name, mime=MIME.get(Path(name).suffix, "application/octet-stream"),
                                width="stretch", key=f"dl_{key}")

    step_word = ("""
3. **Word:** open the *Word copy*, choose your output style in the EndNote tab, then click
   **Update Citations and Bibliography**. Each `{Author, Year #Record}` is plain text until EndNote
   replaces it with a live citation. Then save the document and check it in step 5.""" if "docx" in ss.out_files else "")
    with st.expander("How to finish in EndNote", expanded=True):
        st.markdown(f"""
1. **EndNote:** File > New to create an **empty** library. Then File > Import > File, choose
   `{ss.out_files['ris'][0]}`, Import Option **Reference Manager (RIS)**, Text Translation **Unicode (UTF-8)**.
   (The EndNote XML file is an alternative: Import Option *EndNote generated XML*.)
2. **Check:** show the *Record Number* and *Label* columns. Label holds your reference number.
   Record {int(rec_start)} should have Label 1, record {int(rec_start) + 1} Label 2, and so on.""" +
                    (" If they do not line up, set *First record number* in the sidebar and rebuild." if step_word else "")
                    + step_word + """
4. In EndNote, search Keywords for `refcheck NO_MATCH` to find references that are not in PubMed.

RefCheck does not change your EndNote library or run EndNote: it writes files that you import.
""")

    # --------------------------------------------------------- step 5 -----
    if "docx" in ss.out_files:
        st.subheader("5. Check the result after EndNote (optional)")
        st.caption("Upload the Word document after EndNote has updated the citations. RefCheck reads EndNote's "
                   "citation fields and confirms that each one points to the reference it replaced.")
        fin = st.file_uploader("Document after EndNote update", type=["docx"], key="rt_up")
        if fin is not None:
            fsig = hashlib.sha256(fin.getvalue()).hexdigest()
            if (ss.get("roundtrip") or {}).get("sig") != fsig:
                try:
                    with workspace({"final.docx": fin.getvalue()}) as d:
                        rt = validate_roundtrip(d / "final.docx", refs, out["citation_log"], out["decisions"],
                                                out["records"], preexisting=out["existing_fields"].get("endnote", 0))
                    rep = dict(out["report"], roundtrip=rt)
                    with workspace() as d:
                        write_json(rep, d / "r.json")
                        write_html(rep, d / "r.html")
                        ss.out_files["json"] = (ss.out_files["json"][0], (d / "r.json").read_bytes())
                        ss.out_files["html"] = (ss.out_files["html"][0], (d / "r.html").read_bytes())
                    ss.roundtrip = dict(sig=fsig, result=rt)
                except Exception as e:
                    ss.roundtrip = dict(sig=fsig, result=None, error=str(e))
            rtr = ss.roundtrip
            if rtr.get("error"):
                st.error(f"Could not read that document: {rtr['error']}")
            else:
                rt = rtr["result"]
                if rt["ok"]:
                    st.success(f"All {rt['expected_citations']} converted citations are live EndNote citations "
                               f"pointing to the expected references. The reports now include this check.")
                else:
                    st.error("The EndNote result does not match what RefCheck converted.")
                    if rt["leftover_temporary_citations"]:
                        st.write("Temporary citations EndNote did not resolve:",
                                 ", ".join(rt["leftover_temporary_citations"][:10]))
                    for p in rt["problems"][:10]:
                        st.write("•", p)
                    if rt["citations_missing"]:
                        st.write("Expected but not found:", rt["citations_missing"][:10])
                    if rt["citations_unexpected"]:
                        st.write("Found but not expected:", rt["citations_unexpected"][:10])
                st.caption(f"EndNote citation fields: {rt['endnote_citation_fields']}, bibliography fields: "
                           f"{rt['bibliography_fields']}.")
