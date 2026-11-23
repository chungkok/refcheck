# Privacy

RefCheck is built to check unpublished manuscripts without giving them away. This page says
exactly what leaves the computer, what is stored, for how long, and where the limits are.
It describes the software. It is not a legal assessment: RefCheck has not been assessed against
HIPAA, the GDPR, the Australian Privacy Principles or any institutional security standard, and
makes no claim of compliance with them.

## Two ways to run it

| | Installed on your computer (or your institution's server) | Public demo at refcheck.streamlit.app |
|---|---|---|
| Where the manuscript is processed | on that machine | on Streamlit Community Cloud servers operated by Snowflake, outside your control |
| Who could technically access the file | you (and administrators of that machine) | the hosting provider; see its own privacy terms |
| PubMed answers cached on disk | yes, in your user cache folder (can be switched off) | no |
| Suitable for unpublished or confidential manuscripts | yes, subject to your institution's rules | **no**. Use it for published papers or test files only |

The app detects which case applies from the address in the browser: `localhost`, `127.0.0.1` and
`[::1]` count as local, anything else (or nothing) counts as hosted. Administrators can fix it with
`REFCHECK_DEPLOYMENT=local` or `hosted`. A hosted copy shows a warning in the sidebar on every page.

## What leaves the computer

Requests to NCBI E-utilities (`https://eutils.ncbi.nlm.nih.gov`) when you press *Check all
references* or fetch a PMID you typed, and, when you build the files, requests to the secondary
sources you leave ticked under *Extra checks after PubMed* (all are on by default; the command line
takes `--no-extra` or `--sources`, the environment `REFCHECK_SOURCES=none`).

| Request | Content |
|---|---|
| ESearch | search terms built from **one parsed reference**: DOI, journal, year, volume, first page, first author surname, title words or the title phrase |
| ECitMatch | `journal | year | volume | first page | first author` |
| EFetch | PMIDs only |
| every NCBI request | the tool name `refcheck`, the contact email you entered, and your NCBI API key if you entered one. NCBI asks tools to identify themselves this way. |
| NLM Catalog (NCBI, optional) | a journal abbreviation or ISSN |
| Crossref `api.crossref.org` (optional) | a DOI (as written, or from the matched PubMed record); for a reference not found in PubMed, its title, first author surname, journal and year; your email (Crossref's polite pool) |
| OpenAlex `api.openalex.org` (optional) | a DOI; for a reference not found in PubMed, its title; your email |
| doi.org (optional) | a written DOI that Crossref does not know |
| DOAJ `doaj.org` (optional) | a journal ISSN |
| Unpaywall `api.unpaywall.org` (optional) | a DOI and your email (Unpaywall requires one) |
| Retraction Watch `gitlab.com` (optional) | nothing from the manuscript: a request for the checksum of `retraction_watch.csv`, and the file itself only when GitLab holds a newer version. References are checked against the copy on your computer |

Identifiers taken from a matched PubMed record (its DOI and the journal ISSN) are sent as well as
those written in the manuscript. A source that cannot be reached is tried twice and then skipped
for the rest of the session; the report says which checks are missing.

Never sent anywhere: the manuscript body, tables, figure legends, footnotes, your abstract, the
file name, the file itself, or the reference list as a whole. RefCheck calls no generative AI
service and no service other than those listed above. The optional GROBID PDF reader is a program you run yourself. RefCheck
refuses to send a PDF to a GROBID server that is not on the same computer unless you pass
`--grobid-allow-remote`.

Streamlit's own usage statistics are switched off in `.streamlit/config.toml`
(`gatherUsageStats = false`). Opening the app loads Streamlit's interface from the same server;
no fonts, analytics or scripts are fetched from elsewhere by RefCheck.

**Check it yourself.** *Privacy and network* in the sidebar lists every request of the session with
the exact parameters sent (email and key redacted). The same list is in the JSON report
(`network.requests`) and summarised in the HTML report. The automated test
`tests/test_privacy.py::test_only_reference_list_terms_are_transmitted` fails if any transmitted
term contains a word that is not in the reference list.

## What is stored, and for how long

| Data | Where | Lifetime |
|---|---|---|
| uploaded file | memory of your browser session on the server | until you press *Delete my session data* or upload another file. After you close the tab, Streamlit releases the session when it ends; RefCheck does not control that timing |
| parsed references, results, review decisions | memory of the session | as above |
| the file on disk | a temporary folder created by `privacy.workspace()` | only while one step runs (reading the file, building outputs, the round trip check). The folder is removed in a `finally` block, so also after an error |
| LibreOffice conversion of .doc or .odt | inside that temporary folder | as above |
| output files (RIS, Word copy, reports) | built in a temporary folder, then held in session memory for download | as for the uploaded file |
| PubMed answers | `~/.cache/refcheck/pubmed_cache.sqlite` (local installs only; `REFCHECK_CACHE` changes the path, an empty value disables it) | 30 days per entry, or until *Clear the local PubMed cache*. It holds NCBI's answers and the search terms that produced them (so titles from your reference list), never manuscript text, emails or keys |
| command line outputs | the folder you choose with `--outdir` | until you delete them |

Deleting removes files from the file system. RefCheck makes no claim about recovery from the
storage underneath (SSD wear levelling, file system journals, snapshots, backups, swap, cloud
storage). If that matters, use full disk encryption and your institution's managed devices.

## Logging and errors

RefCheck logs only NCBI request failures by endpoint and error kind (for example
`esearch.fcgi attempt 2 failed: rate_limited`) and cache problems by error type. It does not log
manuscript text, references or search terms. The in app *Search log* shows search terms to you and is kept only in session
memory. The browser is not shown error tracebacks (`showErrorDetails = false`), so server paths
are not revealed.

## Other safeguards

* Upload limit 50 MB (`server.maxUploadSize`).
* File names are reduced to safe characters before use, so a name cannot point outside the
  temporary folder.
* XML from uploaded Word files, NCBI and GROBID is parsed without DTDs or entities
  (`refcheck/safexml.py`), which blocks entity expansion and external entity attacks.
* The original document is never modified. The converter refuses to write over its input.
* Docker: the compose file publishes the port on `127.0.0.1` only, runs as a non root user with a
  read only file system and a memory backed `/tmp`.

## Institutional hosting

If you run RefCheck for colleagues, set `REFCHECK_DEPLOYMENT=hosted` so users see the hosted
notice, put it behind your institution's authentication and TLS, and keep the PubMed cache off or
per user (`REFCHECK_CACHE=`) so one user's search terms are not stored on a shared server. The
privacy position of such a deployment is then the institution's to assess.

## Data flow

See [DATA_FLOW.md](DATA_FLOW.md).
