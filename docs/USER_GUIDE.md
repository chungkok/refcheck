# User guide

## Before you start

* Installed locally, your manuscript stays on your computer and only bibliographic search terms go
  to PubMed. The public demo processes files on a remote server: do not upload unpublished work
  there. See [PRIVACY.md](PRIVACY.md).
* NCBI asks for a contact email. An NCBI API key makes checking about three times faster.

## The workflow in the app

1. **Upload** a Word manuscript (best: citations can be audited and converted), or a PDF, text
   file, CSV, TSV or Excel reference list, a BibTeX `.bib` file, a compiled `.bbl` or a `.tex`
   file with a `thebibliography` section. BibTeX entries are numbered in file order.
2. **Parsed references.** The *parse check* column flags fields RefCheck is unsure of or could not
   find. Correct any cell before searching. Edits are used for the search.
3. **Check against PubMed.** Each reference is searched with its own fields only.
4. **Review the matches.**
   * *Compare one by one* steps through a group (all, matched, need a look, not in PubMed, not yet
     checked, integrity problems). Each candidate is shown next to your reference, field by field,
     with the PubMed abstract and link. Use a candidate, mark the reference as not in PubMed, or type
     a PMID (it is fetched and shown for confirmation). Every choice marks the reference as checked.
   * *All references (table)* lets you search, filter by integrity status, sort, edit PMIDs and
     tick checked. *Accept all yellow suggestions* fills every REVIEW row at once.
   * *Undo last decision* reverses your last change (up to 50 steps).
   * When RefCheck declines to accept a candidate automatically it says why (for example *first
     author differs*).
5. **Build files.** The secondary sources ticked under *Extra checks after PubMed* in the
   sidebar are consulted now (see below). Integrity counts, the citation audit, warnings and
   downloads appear.
6. **Check the result after EndNote** (Word only, optional). Upload the document after EndNote has
   updated it. RefCheck confirms that every converted citation is now a live EndNote citation to
   the right reference.

## What the statuses mean

**Match status** (which publication is it?)

| Status | Meaning |
|---|---|
| matched | one PubMed record clearly fits and nothing conflicts |
| check | a candidate exists but RefCheck abstained or the evidence was not strong enough |
| no match | no PubMed record came close |
| error | the search could not be completed (network or NCBI problem) |
| manual | you chose or typed the PMID |

The score shown with a candidate is an evidence score, not a probability.

**Integrity status** (is the citation correct?)

| Status | Meaning |
|---|---|
| VERIFIED | publication confirmed and details consistent |
| VERIFIED_WITH_WARNINGS | confirmed, with minor differences (year off by one, issue, small title differences) |
| METADATA_ERROR | the right paper, but author, year, journal, volume or pages are wrong |
| DOI_MISMATCH | the DOI in the reference belongs to a different paper |
| PMID_MISMATCH | the PMID in the reference is another paper or does not exist |
| DUPLICATE | the same paper appears earlier in the list |
| RETRACTED | PubMed, Crossref (publisher or Retraction Watch), OpenAlex or your Retraction Watch file reports a retraction |
| EXPRESSION_OF_CONCERN | one of the same sources reports an expression of concern |
| CORRECTED | one of the same sources reports an erratum or correction |
| PREPRINT | a preprint |
| NOT_FOUND_IN_PUBMED | no convincing PubMed record. This alone does **not** mean the reference is wrong |
| NON_PUBMED_SOURCE | found in Crossref or OpenAlex but not PubMed, or looks like a book, report, guideline, software or web page |
| UNRESOLVED | not enough evidence to decide; please check |
| CHECK_ERROR | the check could not be completed |

Every status lists its findings and where each came from.

## Extra checks after PubMed

| Source | What it adds | What it receives |
|---|---|---|
| Crossref | retraction, correction and concern notices (publisher and Retraction Watch); whether the written DOI belongs to this paper; preprint to published links; finds references missing from PubMed | DOIs; for references missing from PubMed, title, first author, journal and year |
| OpenAlex | retraction flag; finds references missing from PubMed; open access link | DOIs, or the title |
| doi.org | whether a DOI unknown to Crossref is registered at all | the DOI |
| NLM Catalog | whether the journal is known to NLM and currently indexed for MEDLINE | journal abbreviation or ISSN |
| DOAJ | whether the journal is an open access journal listed in DOAJ | ISSN |
| Unpaywall | a legal open access copy | DOI and your email |
| Retraction Watch dataset (`retractionwatch`) | retractions, corrections and concerns, checked on your computer | nothing from the manuscript |

**Retraction Watch dataset.** With the `retractionwatch` source on (the default), each run first
asks GitLab for the SHA-256 checksum of `retraction_watch.csv` in
`https://gitlab.com/crossref/retraction-watch-data` (branch `main`, updated each working day at
about 23:00 UTC). If it equals your local copy, nothing is downloaded. If GitLab has a newer
version, the whole file is downloaded, checked against
the advertised checksum, the expected columns and the previous row count, and only then
replaces the copy. If GitLab cannot be reached, or offline mode is on, the last copy is used and
the report says when it was downloaded. The copy lives in the RefCheck cache folder; choose
another with `--rw-dir` or `REFCHECK_RW_DIR`. To use your own file instead (for example a git
clone you update yourself), give its path in the sidebar, with `--rw-csv` or `REFCHECK_RW_CSV`:
it is then used as is and never updated. The JSON and HTML reports record the dataset version
used (record count, download time, checksum).
A source that cannot be reached is reported in the warnings: its checks are missing, not passed.
**When the DOI belongs to another paper.** RefCheck looks the DOI up on its own and compares the
paper it belongs to with the one cited (title, first author, year, journal). A preprint or
published version of the same work, a correction notice for it, or a reworded title is reported
as a warning, not as DOI_MISMATCH. A one or two character typo is reported with the DOI that was
probably intended. *DOI belongs to another work and the cited work was not found* is the
combination to take up with the authors.

*Not found in any source* means only that PubMed, Crossref and OpenAlex returned nothing close;
grey literature, older books and many reports are in none of them.

## Citation audit (Word)

RefCheck finds citations as `[1,2]`, superscript numbers, `(1,2)` (option) and numbers attached
to words such as `diagnosis1,2.` (option, preset when it is the document's main form), in body
text, tables, figure legends, footnotes and endnotes. Gene and genome names (TP53, IDH1, CNS5,
hg19, chr17), exponents and units are never converted. Numbers attached to *grade, stage, figure*
and similar words are left alone and listed for you. Existing EndNote, Zotero, Mendeley and Word
citations are left exactly as they are.

The audit reports citations to numbers not in the list, references never cited, duplicates,
references first cited out of numerical order (matters for Vancouver style), numbering gaps and
mixed citation styles.

## Finishing in EndNote

1. EndNote: File > New (an **empty** library). File > Import > File, choose the `.ris` file,
   Import Option *Reference Manager (RIS)*, Text Translation *Unicode (UTF-8)*. The `.xml` file is
   an alternative (Import Option *EndNote generated XML*).
2. Show the *Record Number* and *Label* columns. Label holds your reference numbers. Record 1 should
   have Label 1, and so on. Importing into an existing library changes record numbers: then untick
   *Include EndNote record numbers* and rebuild, and EndNote will ask whenever a citation is
   ambiguous.
3. Word: open the *Word copy*, pick an output style, click *Update Citations and Bibliography*.
   Until then each `{Author, Year #Record}` is ordinary text.
4. Save, then use step 6 in the app (or `--roundtrip` on the command line) to confirm the result.

RefCheck does not change your EndNote library or run EndNote.

## Reports

| File | Content |
|---|---|
| `*_integrity_report.html` | readable report: summary, every reference with findings and sources, citation audit, Word changes, round trip, network activity |
| `*_integrity_report.json` | the same, machine readable, with software version, thresholds, timestamps and the full network audit |
| `*_pubmed_report.xlsx` / `.csv` | one row per reference with every parsed and PubMed field; extra sheets for summary and in text citations |
| `*_references.bib` | BibTeX of every reference (PubMed metadata when matched) |
| `*_pmids.txt` | PMIDs, one per line |

The Excel and CSV reports also have `suggested_reference` (the matched record in Vancouver style)
and `open_access_url`.

The reports keep automatic results and your decisions apart (`decided_by`, `user_decision`).

## Command line

```
python refcheck_cli.py manuscript.docx --email you@example.org
python refcheck_cli.py papers/ --email you@example.org --outdir results        # every file in a folder
python refcheck_cli.py manuscript.docx --email you@example.org --audit-only    # no Word copy
python refcheck_cli.py manuscript.docx --email you@example.org --offline       # cached answers only
python refcheck_cli.py manuscript.docx --email you@example.org --pmid 6=12345678 --pmid 9=
python refcheck_cli.py --roundtrip results/manuscript_integrity_report.json final.docx
python refcheck_cli.py paper.pdf --email you@example.org --grobid http://localhost:8070
python refcheck_cli.py refs.bib --email you@example.org --no-extra             # PubMed only
python refcheck_cli.py manuscript.docx --email you@example.org --sources crossref,nlmcatalog,retractionwatch
python refcheck_cli.py manuscript.docx --email you@example.org \
       --rw-csv retraction-watch-data/retraction_watch.csv     # your own copy, not updated
```

## Troubleshooting

| Problem | What to do |
|---|---|
| No references found | enter the section heading your document uses (sidebar or `--heading`) |
| Two references merged | check the warning about numbering jumps; fix the source document or edit the parsed table |
| Many *error* results | NCBI was busy or unreachable: run again, add an API key |
| A correct paper shows *check* | read the reason shown; accept the candidate if it is right |
| Citations not converted | check the citation options in step 4; existing reference manager fields are never converted |
| EndNote links citations to the wrong records | import into an empty library, or untick record numbers and rebuild |
