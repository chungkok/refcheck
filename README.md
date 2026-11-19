# refcheck

Intelligent Reference Extraction, Validation & Synchronisation

refcheck streamlines academic reference management by extracting references from documents, verify every reference in PubMed, record its PMID,
show which ones have no match, and hand the whole set to EndNote. For Word manuscripts it also
writes a copy in which the numbered citations are replaced by EndNote temporary citations, so
EndNote can take over the in text citations and rebuild the bibliography.

<p align="center">
  <a href="https://refcheck.streamlit.app/">
    <img src="https://img.shields.io/badge/🚀_Launch-Live_Demo-brightgreen?style=for-the-badge" alt="Launch CiteBridge">
  </a>
</p>

<p align="center">
  <strong>🌐 <a href="https://refcheck.streamlit.app/">Access CiteBridge — Live Application</a></strong>
</p>

## Start

```
run_app.bat            (Windows: double click)
./run_app.sh           (Linux, macOS, WSL)
```

The app opens at http://localhost:8501. Enter your email in the sidebar (NCBI asks every tool for a
contact address). An NCBI API key is optional and makes searching about three times faster; you
can create one under Account settings at ncbi.nlm.nih.gov. Both can also be set as the
environment variables `NCBI_EMAIL` and `NCBI_API_KEY`.

Command line, same engine:

```
python refcheck_cli.py manuscript.docx --email you@example.org
python refcheck_cli.py refs.pdf --email you@example.org --outdir results --debug
python refcheck_cli.py manuscript.docx --email you@example.org --pmid 6=12345678 --pmid 9=
```

## Input files

| File | Notes |
|---|---|
| .docx | Best choice. Typed numbers or Word automatic numbering. Citations in the text can be converted. |
| .doc .odt .rtf | Converted with LibreOffice if installed, otherwise save as .docx first. |
| .pdf | Uses the text layer; scanned PDFs need OCR first. Manuscript line numbers, running headers and watermarks such as "In review" are removed automatically, and letter spaced URLs are rejoined. |
| .txt .md | Plain text. |
| .csv .tsv .xlsx | One reference per row. A citation column is found by name, or separate columns (title, authors, journal, year, volume, issue, pages, doi, pmid). |

The reference section is found by its heading (References, Bibliography, Literature cited, Works
cited). Reading stops at Figure legends, Tables, Supplementary, Acknowledgements, publisher notes
and similar headings. Recognised styles:

| Style | Example ending |
|---|---|
| Vancouver / AMA | `Neuro Oncol. 2025;27(8):2035-2046.` |
| No period before the year | `J Clin Oncol 2025;40:1-10.` |
| Frontiers | `Semin Oncol (2016) 43(6):676-81.` |
| EndNote online first output | `Sci Rep. 15. England(2025). p. 2204.` |
| APA | `Smith, J. (2020). Title. Journal, 12(3), 45-67.` |
| Preprints, reports, web pages | parsed as far as possible; usually not in PubMed |

PMIDs and DOIs written anywhere in a reference are picked up and used first. The parsed table can
be edited in the app before searching.

## What you get for each kind of file

| Input | Review in the app | Downloads |
|---|---|---|
| Word manuscript (.docx) | yes | EndNote import (.ris), Word copy ready for EndNote, report (.xlsx, .csv), PMID list |
| PDF, text, CSV, Excel, or a Word file that is only a reference list | yes | EndNote import (.ris), report (.xlsx, .csv), PMID list |

## Reviewing the matches

After the PubMed check, step 3 has two views.

**Compare one by one.** Pick a group (all, matched, need a look, not in PubMed, not yet checked) and
step through it with Previous and Next. For each reference you see the best PubMed match and the
runner up next to the fields from your document (title with % similarity, first author, journal,
year, volume, issue, pages, DOI), each marked same or different, plus the PubMed abstract and link.
Then choose:

* **Use PMID …** for the best match or the runner up
* **Not in PubMed** to import the reference as written
* **type a PMID** you found yourself; it is fetched and shown side by side so you can confirm it

Each choice marks the reference as checked.

**All references (table).** Every row with status, PMID going to EndNote, title similarity, both
titles and a PubMed link. Edit the PMID or tick checked directly; accept all yellow suggestions in
one click.

The report records what you decided and whether you checked each reference.

## How each reference is matched

1. A PMID written in the reference
2. The DOI
3. NCBI citation matcher (journal, year, volume, first page, first author). This works even when
   the title differs from PubMed, for example superscripts such as Rapid CNS²
4. Journal, volume and page search; first author, year and page search
5. Title phrase, then each title word tagged [Title], with and without author and year

Every candidate is scored on title similarity, first author, year, journal, volume, first page,
DOI and PMID. Searching stops at the first confident match.

| Status | Meaning |
|---|---|
| AUTO_MATCH | score 86 or more and clearly ahead of the runner up |
| REVIEW | score 70 to 85; a suggestion is shown for you to accept or replace |
| NO_MATCH | nothing close; usually preprints, books, reports, or a citation error |
| MANUAL | you supplied or accepted the PMID |
| ERROR | PubMed could not be reached for this reference |

## Outputs

| File | Use |
|---|---|
| `*_endnote.ris` | Import into EndNote. Every reference is included: matched ones with full PubMed data and abstract, unmatched ones as written in your manuscript. |
| `*_endnote_ready.docx` | Word copy with citations replaced, e.g. `[2,3]` becomes `{Patel, 2022 #2; Deacon, 2025 #3}`. |
| `*_pubmed_report.xlsx` / `.csv` | One row per reference: status, PMID, link, whether you checked it, whether it is cited in the text, score, PubMed versus parsed fields, runner up. For Word input a second sheet lists every in text citation found and anything left unchanged. |
| `*_pmids.txt` | The PMIDs, one per line, for PubMed batch search or other tools. |

In EndNote each record carries:

* **Label**: your manuscript reference number(s)
* **Accession Number**: the PMID
* **Keywords**: `refcheck AUTO_MATCH`, `refcheck MANUAL` or `refcheck NO_MATCH`
* **Notes**: the original reference text

If the same paper appears twice in the list, both numbers point to one record (Label shows both).

## Finishing in EndNote and Word

1. In EndNote, File > New to make an **empty** library.
2. File > Import > File. Choose the `.ris` file, Import Option **Reference Manager (RIS)**, Text
   Translation **Unicode (UTF-8)**.
3. Show the Record Number and Label columns. Record 1 should carry Label 1, record 2 Label 2, and
   so on. If your library numbers start elsewhere, set *First record number* in the app (or
   `--record-start`) and rebuild the Word copy.
4. Open `*_endnote_ready.docx` in Word, pick the output style in the EndNote tab, then click
   **Update Citations and Bibliography**. EndNote turns each temporary citation into a live one and
   writes the bibliography at the end of the document.

The record number is what tells EndNote exactly which reference is meant. If you would rather
import into an existing library, untick *Include EndNote record numbers*; the citations become
`{Patel, 2022}` and EndNote asks you to choose whenever more than one record fits.

If you changed the temporary citation delimiters in EndNote's Cite While You Write preferences,
set them back to `{ }` and `#`, or the citations will not be recognised.

## What the Word conversion does and does not touch

The app counts each citation form in your document and presets the options to match.

| Form | Example | Default |
|---|---|---|
| Square brackets | `[1]` `[1,2]` `[1-3]` `[1, 3-5]` | on |
| Superscript | ¹ ¹ʼ² ¹⁻³ | on |
| Parentheses | `(1,2)` | on only when it is clearly the main form |
| Attached to words | `classification1.` `diagnosis1,2.` | on only when it is clearly the main form |

The last form appears when superscript formatting has been lost. Numbers that belong to the word
are never treated as citations: gene and genome names (IDH1, TP53, CNS5, H3K27me3, hg19, chr17,
rs IDs) and anything outside the reference list. A number stuck to a word such as grade, stage,
phase, type, figure or table (`WHO grade37`) is ambiguous, so it is left unchanged and listed for
you to decide.

Body text, tables and figure legends are all included. Also left unchanged and listed in the
report:

* numbers outside the reference list, such as `[12]` in a list of 8
* superscripts that look like exponents or units (`10⁶`, `mm²`, `Ca²⁺`)
* citations that are already EndNote, Zotero or Word cross reference fields

The report also lists references that are never cited in the text, and the app warns when the same
paper appears twice in the reference list. The original file is never changed.

## Tests

```
python tests/test_offline.py
```

Uses real PubMed records for five references saved as fixtures, so it runs without network access.
