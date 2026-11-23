# Changelog

All notable changes. Dates are UTC. Entries that name `benchmark/`, `validation/` or the stress
test scripts refer to the full validation package that goes with the methods paper; they are
not part of this repository.

## 2.0.0.dev0 (unreleased, 21 November 2026)

- Known limit (not fixed): in the Word copy of a letter written by a program other than Word (docx.js), EndNote's Cite While You Write tab was greyed out, although the copy opened normally and the EndNote round trip succeeded once its content was in a Word made document. Adding the parts and settings Word writes did not help (tested in Word, 23 November 2026), so no change was kept. Workaround: copy the whole text into a new Word document, or open the copy in Word and use Save As. The round trip on that letter was OK (15 of 15 citations live EndNote citations to the expected references).

- Found by two real manuscripts run live (93 references from a PDF, 14 from a Word letter):
  - Style check: a list from one EndNote style, 'Journal (Year) Vol(Iss):Pages' with 'Journal. Vol. Country(Year). p. Pages' for records imported from PubMed, was reported as four mixed styles with the advice to check for references pasted from a generator. Both forms are now read as the Vancouver family (styles 'Frontiers (numbered)' and 'EndNote output').
  - Author check: a group name in PubMed's author list (PMID 33256715, 'Precise4Q consortium') no longer counts as an author the reference left out.
  - Numbers attached to words: a short capitalised name with a number ('Molm13', a cell line) is left for the user to check rather than converted.

- PDF line numbers: Word prints them glued to the line end. When a line ended with a word or a page range broken at a dash, the number was kept ('State-536 of-the-Art', 'p. 163–614 70.', a DOI 'S2589-743 7500...'). Once the numbering sequence is established the expected number is now removed after a dash too. Found in a real 93 reference manuscript (six references affected); all 93 are now read intact.

- Stress mocks round 8 (make_stress_mocks8.py: reference errors, numbering faults, collective authors, references without titles, superscript variants, .txt/.md/.csv/.xlsx inputs, broken files, styled references, citations in odd places). Found and fixed:
  - References without a title ('Mahon FX, et al. Lancet Oncol. 2010;11:1029-35.'), and references given only by a DOI or a PMID, were never matched: the author list was read as the title and no search was run. They are now read and matched when every field they give agrees (scoring documented in docs/ALGORITHM_SPECIFICATION.md), and reported VERIFIED_WITH_WARNINGS with 'reference incomplete'. A DOI's digits are no longer read as the year. In the 800 case benchmark, category 15 (incomplete references) now resolves 35 of 50 to the right PMID instead of 29, none wrong.
  - A last page that differs ('1029-42' for '1029-35') is reported (last_page_differs, minor); before, only the first page was compared.
  - A reference that leaves out the first author, or cuts the title short, is matched and the error reported; it was left unresolved.
  - Collective authors: PubMed's first author 'Cancer Genome Atlas Research Network' was cut to 'Cancer Genome Atlas Research', and 'The Cancer Genome Atlas ...' was reported as a different first author.
  - Numbering: a number typed twice is read as the next reference with a warning (the two were merged); two references typed into one paragraph are split when the next number is missing from the list; a bulleted list is read as one reference per bullet.
  - Superscript citations that hold their brackets or punctuation ('[1]', '(8,9)', '2,', '13.') are converted, the punctuation kept.
  - A Markdown heading '## References' is recognised.
  - A scanned PDF with no text layer and a password protected PDF now stop with a clear message; they gave 'no References heading found' and a pypdf error.
- run_stress_mocks.py covers rounds 1 to 8 (30 manuscripts, 63 files, and the broken files).

- Stress mocks round 7 (make_stress_mocks7.py: AMA, NEJM, Lancet, BMJ, PLOS, Cell, Harvard, IEEE, ahead of print references, a thesis; each DOCX and PDF). All eight journal styles were read and matched with no false alarms. Found and fixed:
  - A thesis or book with one reference list per chapter: the citations of every chapter were converted against the one list RefCheck checked, so chapter 1's [1] became chapter 2's reference 1. RefCheck now says the document has several reference lists, converts only the citations of the chapter whose list it checked, and asks for each chapter to be checked as its own file. A reference list also ends at a 'Chapter N' heading or at the next reference heading.
  - The reference text in reports is kept as written; 'Published online Apr 5, 2015' was shown rewritten as '2015'.
  - Warnings from reading the file (an unnumbered list, several reference lists, no heading found) are now in the integrity report (reading_warnings, JSON and HTML); before, only the command line and the app showed them.
- run_stress_mocks.py builds all 21 stress mocks and runs them as DOCX and PDF, offline or with --live EMAIL, and prints one line per file.

- Stress mocks rounds 4 to 6 (make_stress_mocks4/5/6.py: imported text, an EndNote formatted manuscript, a 120 reference two column review, Word markup around citations; each DOCX and PDF) found and fixed:
  - Soft hyphens and zero width characters are removed and ligatures spelt out in reference text; a title word broken at a PDF line end ('ide-ntified') no longer gives a title wording warning.
  - NLM notes after the citation ('PMCID: ...', 'Erratum in: Blood. 2021;...', 'Comment in:', 'Retraction in:') are no longer parsed as the citation itself; before, the erratum's year and pages were read and the reference was flagged or taken for a book.
  - The heading 'References and Notes' (Science), 'Notes and References' and 'References Cited' are recognised.
  - Citations already held by EndNote, Zotero, Mendeley or Word citation fields are counted by the citation audit, so their references are no longer reported as never cited; they are still left as they are. The reference list removed with its EN.REFLIST field no longer trips the structure check.
  - PDF: a wrapped line repeated in several references ('discontinuation in chronic myeloid') was dropped as if it were a watermark; watermarks and running heads must now sit at the same height on at least three pages and half of them. A Greek letter that the text layer moves to the end of its line is put back into the single hole it left ('interferon-α2b').
  - A citation shown by a HYPERLINK field ('[5]' linked to the list, as PDF converters write it) is converted; it was left silently. A citation in a text box is converted and the warnings say to check it in EndNote.
- Known limit: in a PDF, a floating text box is written at the end of its page, so its text is added to the reference printed last on that page (the match is unaffected).

- Stress mocks rounds 2 and 3 (make_stress_mocks2.py, make_stress_mocks3.py; five manuscripts, each DOCX and PDF) found and fixed:
  - IEEE ranges '[4]–[6]' now cite 4, 5 and 6; before, 5 was lost.
  - A superscript right after a one letter symbol (R², I², χ²) is taken as a power, not a citation.
  - Numbers attached to words: a citation after a short common word ('follow up7') is converted; 'week24', 'day7', 'cohort3' and similar are left for the user to check.
  - A reference list kept in a Word table (number | reference) under the heading is read, and the table is removed from the Word copy (other tables are kept; the structure check allows for it).
  - A reference list numbered '1<tab>Author' with no full stop (EndNote numbered styles) is read as numbered; the number is no longer taken into the first author.
  - An unnumbered author year list in a PDF or text file is split where a line opens with an author name after the end of a reference; before, a whole PDF list was read as one reference. A DOI broken over two lines ('...00607-' / '6') keeps its last part.
  - APA: a journal name that holds commas or brackets ('Journal of immunology (Baltimore, Md. : 1950), 182(1), 240-9') is kept whole.
  - Matching: a first author surname one typing slip away from PubMed's ('Slmaon' for 'Slamon') counts as close, not different, so a reference agreeing on title, journal, year, volume and page is matched and the author is flagged; before, it was left unresolved.
  - PDF: footnotes at the foot of a page are no longer read into the reference printed above them; page margin lines that differ only in digits ('2013;369(1):32-42.') are no longer taken for running heads.
- check_mock.py: checks every reference for false alarms, made up references for no match, citation audit findings, footnote and endnote citations, tracked deletions, and records shared by duplicate references.
- Benchmarks and the five datasets unchanged; 326 tests pass.

- Word converter: a superscript citation at the very end of a paragraph or table cell ('...in registries.¹⁷' with nothing after it) was taken for an exponent and left unchanged; it is converted now. Exponents (10⁹/L, mg/m², P < 10⁻⁵) are still left alone.
- Word converter: superscripts before the first Abstract, Summary, Introduction or Background paragraph (author and affiliation marks on a title page, 'Ana Silva¹,²') are no longer converted to citations; the run warnings say how many were left. A document without such a heading is converted as before.
- Word converter: a lone year in brackets or parentheses, '(2019)' in a list shorter than 2019, is no longer reported as a citation to a missing reference.
- PDF reading: line numbers that the text layer gives as a block of their own after the page text (LibreOffice and some Word exports) are dropped before the line number stripper runs; before, it took the digits at the end of a DOI for line numbers (10.1001/jamaoncol.2015.2010 read as ...2015.20). A running head or page footer found at the top or bottom of at least two pages is dropped in short PDFs too (it was glued to the last reference of page 1 in a two page article).
- Step 6 stress test: make_stress_mocks.py builds two more mock manuscripts, each as DOCX and PDF, citing 34 PubMed papers and one source outside PubMed with two deliberate errors: a submission style manuscript (Letter size, double spaced, line numbers, title page, citations in parentheses, Frontiers and EndNote country year references) and a typeset two column article (superscript citations, exponents, table footnote marks, running head, Nature and Science style references). check_mock.py now checks that deliberate errors are flagged, takes --no-word for PDF input and checks superscripts left unconverted; tests/test_stress_mocks.py runs all four offline. Benchmarks and datasets unchanged.

- PDF reading: a word, name or page range broken after a hyphen at a line end ('Next-' / 'Generation', 'Reina-' / 'Ortiz', '610-' / '4') is joined without a space; before, only a lower case continuation was. Found by the step 5 long list test (303 references, DOCX and one and two column PDF, all extracted in order).
- Step 5: make_long_review.py and check_extraction.py build a long reference list as DOCX and PDF and check that every reference is extracted, in order, with its text intact.

- Warnings: references left CHECK_ERROR because PubMed did not answer are now named in the run warnings (report, app and command line), with the advice to run again; before, only their status showed it.
- Step 5: check_run.py reads a run's integrity report for the manual robustness checks (throttling retries, no reference accepted while its check failed, CHECK_ERROR and its warning, one manuscript per report); the README gives the commands.

- Round trip check: EndNote writes a long citation as an EN.CITE field holding a nested EN.CITE.DATA field, with the same base64 citation XML on both, padded with NUL bytes. The check counted the outer field as a second, empty citation and could not parse the padded XML (34 fields and 8 unreadable for 20 citations in the step 6 mock). It now reads each citation once and cuts the XML at </EndNote>. Tested with the mock as EndNote updated it (tests/data, author metadata and labels removed) and a simulated nested field.

- EndNote export: a reference not in PubMed gets its record type from what it is (BOOK, CHAP, RPRT, ELEC, THES, CONF, COMP; JOUR for a journal article), with URL, access date, publisher, place, edition and, for a chapter, the book title, in RIS and EndNote XML. A web page 'Organisation. Title. Year. Available from: URL' is read with the organisation as author (it was read as the title, and the title as a journal). Found by the step 6 mock manuscript.
- Step 6: make_mock_manuscript.py and check_mock.py build a 28 reference mock manuscript and follow every in text citation to its EndNote record, PMID and record type; tests/test_mock_manuscript.py runs it offline.

- Source type: the words that mark a book, report, guideline, abstract or software are searched outside the title, so a journal article titled '...: a report from the IFM' that is not in PubMed is reported as not found (a strong signal), not as a non PubMed source. Found by the Gemini myeloma set (#64). Benchmarks and datasets unchanged.

- Review screen and reports: the side by side table has an Authors row (the listed authors against PubMed's, naming any not found), and the Year row shows '✅ online year (print YYYY)' when the reference cites the electronic publication year. The CSV and Excel reports add pubmed_authors and pubmed_online_year; the HTML report shows the full PubMed author list beside any author finding. Display only: matching and results are unchanged.

- Author check: a double surname that PubMed indexes under its last part, with the rest folded into the initials ('Gómez Casares MT' is 'Casares MTG' in PubMed 33707652), is no longer reported as an author missing from the record. Found by the Gemini set.
- Step 3: labels.csv takes the labels genuine, genuine_error, garbled and fabricated, an expected PMID and the evidence; the summary reports each label and every wrong PMID. gemini_cml_labels.csv labels the Gemini set, each reference checked against PubMed.

- Reference reader: a list number with no space before the author ('1.van de Donk', '1.Smith', as pasted from a PDF) is removed; '2.5 mg' and '3rd ed.' on a wrapped line are not taken for numbers. AMA 'Journal. Published online [date] Year.' is read as journal and year. A journal name holding commas in Harvard ('Clinical Lymphoma, Myeloma & Leukemia, 17(9), pp.') is kept whole. In APA a title with a '?' and a subtitle, or 'vs.', is no longer cut short. Styles benchmark (offline): false alarms 9 to 2, no PMID 6 to 3, 0 wrong; the 800 case benchmark and the five datasets are unchanged.
- Styles benchmark: the builder now drops the list number before a lower case particle too; the 27 affected lines in styled_references.jsonl are corrected. S118's seed lacks one of PubMed's four authors; see benchmark/styles/README.md.

- Matching: a PubMed record typed both 'Retraction Notice' and 'Retracted Publication' (an Elsevier withdrawal, where one record is the withdrawn article and its notice) is no longer treated as a notice, so a reference to it can match automatically; it is still flagged as retracted. Every other notice type still goes to review. Found by the fresh step 4 hard cases (PMIDs 33387564 and 39614461).

- Reference reader: a year followed straight by a locator with no volume (a PII such as 2024:S0190-9622(24)02755-5, a Research Square or arXiv ID, a bioRxiv number or an article number) now keeps the journal and reads the locator as the page field instead of a volume. Found by the step 4 hard cases: 7 of 213 were left unresolved, all of this form.
- Nature and Science author lists: surnames with lower case particles (van den Bent, de Vries, von Lilienfeld-Toal) and names written as 'Li C-I' no longer push the last author into the title. When both readers fit, the one with the longer author list wins. Styles benchmark false alarms 23 to 9; the 800 case benchmark and the five datasets (offline) are unchanged.

- Validation scripts: sensitivity.py now counts a real reference false alarm exactly as score_datasets.py does (a cited preprint given PREPRINT is correct, not an alarm) and names the references it counts; fit_cross.py recommends the evidence weights only when they beat the hand set points on the held out data.

Development branch `refcheck-2.0`. Not yet validated on a benchmark.

### Added
* Step 10: diff_runs.py (what changed between two runs, per reference); confirmed.csv accepts
  "no" with the right PMID and several acceptable PMIDs (a preprint as cited and its published
  version); a PREPRINT status on a reference that cites a preprint server is correct; category 11
  counts any of the fields the gold standard lists as altered; three more adjudications (the
  altered year is the paper's online publication year, PubMed); --refcheck accepts the pre
  dataset zip as it is and explains a wrong path.
* Threshold sensitivity analysis (validation/step10_datasets/sensitivity.py): each checking
  threshold varied on its own, and the two new rules switched off, on all five datasets and the
  123 genuine references. The thresholds are now named constants (verify.DUP_TEXT_RATIO,
  DUP_MIN_NUMBERS, DUP_MIN_LETTERS; textutil.VARIANT_RATIO; doicheck.TITLE_AGREE), values unchanged.
* validation/step10_datasets/adjudications.csv: four OpenAI 800 category 10 labels where the gold
  standard's own intended authors equal the reference; scores are reported as labelled and after
  adjudication.

### Changed
* Step 10: the OpenAI sets are development data since their failures were read (22 November
  2026); score_datasets.py shows every generated set's failures by default.
* Step 10, the five reference datasets (validation/step10_datasets, tests/test_datasets.py):
  import_datasets.py (real references, Claude 800 v2 and 100 v2, OpenAI 800 and 100 into one gold
  format, with the same rules for both generators), run_datasets.py (live and cached, as the app
  runs, or an offline snapshot dry run; --refcheck runs another copy of the code, such as the
  version tagged v2.0.0-pre-datasets), confirm_real.py (confirmation sheet for the real PMIDs),
  score_datasets.py (per set and category with Wilson intervals, wrong PMIDs, development and test
  failures kept apart) and fit_cross.py (weights fitted on one generator, tested on the other,
  and a final fit). Validation app tab "10 Five datasets".
* fit_weights.py: --test-sources, references marked not for fitting, held out results by source
  and category, evaluation.json.

### Fixed
* Latin letters that Unicode does not decompose (Þ, Ð, Đ, Ł, Ø, Æ, Œ, ß, ı) were dropped from
  names and titles, so 'Óskarsson JÞ' lost its initials and 'Óskarsson J' was reported as not
  among the PubMed authors. They are now transliterated (textutil.fold_letters). Found on the
  real references.
* 'J Vis Exp. 2026(234).' (year then issue, no volume, as EndNote writes issue numbered
  journals) was not read: the journal was lost and the right candidate stayed below the automatic
  threshold (85, now 93). Found on the real references.
* Authors sharing a surname ("Gupta A, Gupta R", "Luo J, Luo J") were matched to the same PubMed
  author, so the second was reported as missing (authors omitted, major) or the list as out of
  order. Found on the real references: 8 of 202 correct references were flagged. Each listed
  author now takes the next unused PubMed author with that surname, preferring matching
  initials. Also removed 26 false authors omitted and 18 false author order findings from the
  generated sets; no real error was lost.
* Two references with the same title and year but a different first author and a different
  volume or pages were reported as the same publication cited twice. They are now a repeated
  title to check (minor), not a duplicate. Found by the threshold sensitivity analysis on the
  OpenAI 100, whose generator reused title templates.
* Duplicates whose title cannot be read (a copy with its punctuation removed, for example) were
  not linked. References with the same numbers in the same order and near identical wording are
  now duplicates; references resolved to different PMIDs are never merged. Found on the OpenAI 100.
* A DOI belonging to another paper in the same title series ("Acute myeloid leukemia: 2023
  update ..." against "Multiple myeloma: 2024 update ...") was reported as an alternative DOI for
  the same work. Similar titles that swap a content word or a number are now different titles.
  Found on the OpenAI 800.
* Authors left out of a complete list, or an invented author added, were not reported (found on
  the Claude 800 development set, category 10, 12 of 50 missed offline). A gap in the listed
  authors, and a list that stops early without "et al." when PubMed has six or fewer authors,
  are reported as authors omitted. An author missing from PubMed is reported when the record is
  from 1996 or later, when MEDLINE began listing every author. The old 800 case benchmark and the
  123 genuine references are unchanged.
* Test fixtures carried shortened PubMed author lists; they now hold the full lists from PubMed.
* Citation style detection (`refcheck/styles.py`, tests/test_style_detection.py). Each
  reference gets a style family (Vancouver family, APA, Nature, Science, Cell or Frontiers,
  Harvard, Chicago, IEEE, MLA, MDPI, Bioinformatics before 2023) and a best guess at the exact
  style. When the marks in one reference do not decide, the guess is a tie such as
  "AMA / Vancouver (NLM)". The manuscript's style is the most common family, and the exact
  style is pooled over its references. Shown in the HTML and JSON report, the CSV and Excel
  report (detected_style_family, detected_style) and the app.
  Development measurement (benchmark/styles/evaluate_style_detection.py, CSL references): family
  right for all 2,640 references of 15 styles and all 3,168 of 18 layouts; exact style right or
  tied for all, a single exact answer for 85.6% and 88.0%. On the 123 genuine references, all
  were Vancouver family, best guess AMA. Not validated on typed references in other styles.
* Mixed style warning: when a second style family has at least 3 references and 10% of the
  recognised references, the report and the app warn and list them. It says mixed styles are a
  reason to look, not evidence that a reference is wrong. In simulated manuscripts from the CSL
  references (500 per cell): no false warning in single style manuscripts of 20, 40 or 80
  references, and a warning in every manuscript with 4 or 8 of 40 references in another family.
* The labelling workbooks carry the detected style (detected_style, style_confirmed), key.csv
  the manuscript's style, and score_realworld.py reports results by style.
* Evidence weight kit for real and generated sets (validation/step8_evidence_weights):
  build_real_set.py (200 PubMed papers written in 3 styles each, or typed references with
  PMIDs; papers of the 800 case benchmark refused), generation/PROMPTS.md and batch_plan.csv
  (800 references each from Claude and ChatGPT, 16 topics and styles), join_batches.py, and
  run_sets.py (live runs, review sheet for generated references that resemble real papers).
  fit_weights.py: --calibration-sources (prior and calibration from real references only),
  papers kept together in the split and bootstrapped as clusters, references awaiting review
  left out, and held out results by citation style.

### Changed
* Validation plan: step 2 is now one final manuscript, labelled blind and run once with the
  code and weights frozen. The two annotator protocol stays available. The validation app's
  step 2 and step 8 tabs follow the new plan.
* Any citation style (`benchmark/styles/`, tests/test_styles.py). New readers for quoted titles
  (Harvard, Chicago, IEEE, MLA), Nature and Science, and author then year styles (Cell,
  Frontiers, older PLOS). The parse that reads the most fields plausibly is kept. Journal
  abbreviations are kept in the journal. Matching also looks for the candidate title inside
  the raw reference text and the first author at its start, and searches by the reference's
  six most distinctive words when the fields are thin (plan step `text-words`).
  Development benchmark of 2,640 CSL references in 15 styles: right PMID 59.5% before,
  99.7% after, with 0 wrong PMIDs both times (offline snapshot; development only, not validated).
  Both Bioinformatics (OUP) layouts added (current, and before 2023 with "Li,H. and Durbin,R."),
  176/176 each. Pages read as article numbers with letter prefixes (lqaf041, pgae456) and Cell's
  228–246.e17, and "and" or "&" inside an author list separates authors.
  MDPI reader (semicolon author lists, "Journal Year, Volume, Pages"): 1% right PMID before,
  99% after (174/176). More ISO 4 abbreviation words (Front., Genom., Bioinform. ...), and a
  parse that leaves journal abbreviations at the end of the title loses to a cleaner one.
* Cancer reference benchmark tooling (`benchmark/cancer800/`): 800 problematic references in
  16 categories built from 176 seed publications verified in PubMed, with a gold standard,
  automated validation and a scorer. Development results only (see its README).
* Checks prompted by that benchmark: word level title comparison (`title_wording_differs`,
  `title_word_order_differs`); author list comparison (`first_author_initials_differ`,
  `authors_not_in_record`, `author_order_differs`, `authors_omitted`); `pmid_not_found` for a
  written PMID that does not exist (previously reported as another paper's PMID);
  `reference_incomplete`; `doi_not_in_pubmed` for unmatched references.
* Checks prompted by a held out set of 100 fictional references written by ChatGPT
  (`benchmark/heldout_chatgpt100/`): `volume_not_in_pubmed` (major) when PubMed has the cited
  journal around the cited year but not the cited volume (two searches, unmatched references
  only, `REFCHECK_DISABLE=volume-check` turns it off); `title_repeated` (minor) when the same
  title is cited again with another year.

* Retraction Watch dataset kept current automatically (`rwdata.py`, source `retractionwatch`,
  on by default): each run requests only the checksum of `retraction_watch.csv` from
  gitlab.com/crossref/retraction-watch-data and downloads the file only when it changed. A
  download is used only after its SHA-256, columns and row count pass; otherwise, offline, or
  when GitLab is unreachable, the previous copy is used. The reports record the version used.
  New settings `retraction_watch_dir` (`REFCHECK_RW_DIR`, `--rw-dir`). A file given with
  `--rw-csv` is still used as is. Not yet tested against the live GitLab service.

* Retraction Watch: a reference with no DOI that PubMed cannot match is looked up by exact
  title and first author surname; a notice whose reason says "Updated to Retraction" counts as
  a retraction; a retraction or concern followed by a reinstatement is reported as reinstated.
  Tested on the dataset downloaded on 21 November 2026 (73,965 records): references built from
  its rows were flagged RETRACTED in 1,100 of 1,100 with a DOI or PubMed match and 193 of 200
  with neither (the rest have titles under 30 characters); 0 of 1,000 same title, other author
  references and none of the user's 123 real references were flagged.

* Validation kit (`validation/`, runbook in its README): live run suite with five references of
  known outcome and a run comparer (step 1); blinded labelling workbooks, a frozen run recorder and
  a scorer with Cohen's kappa, adjudication and Wilson intervals for real manuscripts (step 2);
  a runner and fixed prompts for fabricated sets from other generators (step 3); a hard case
  builder from live PubMed (errata, retractions, concerns, preprints, online first, common
  surnames) with its scorer (step 4); offline stress tests `tests/test_stress.py` (step 5); an
  output checker for the Word copy and RIS (step 6); an ablation runner and a comparison scorer
  for other tools (step 7). None of these has been run against the live services yet.
* Validation app (`validation_app.py`, started with `run_validation.sh` or `run_validation.bat`):
  every validation step in the browser, with uploads, live progress and downloads, running the
  same scripts as the command line; local use only. The frozen run manifest now records a
  checksum of RefCheck's code, so a run from a copy without git is still identified.

* Evidence based candidate scoring (`refcheck/evidence.py`, `benchmark/fit_weights.py`, validation
  step 8). Every candidate's field comparisons are recorded as levels in the match results, the
  JSON report and the benchmark outputs (`candidates.jsonl`). `fit_weights.py` estimates
  likelihood ratio weights log2(m / u) per level with 95% bootstrap intervals, a prior and a
  logistic calibration from labelled live runs. It reports calibration and compares with the
  hand set points on held out references. `--scoring evidence --weights FILE` (or
  `REFCHECK_SCORING`, `REFCHECK_WEIGHTS`) uses them, with probability thresholds. The default is
  still the hand set points, until weights are fitted on live data. The points are rebuilt
  exactly from the recorded levels on all 1,115 benchmark candidates. Step 2 now sets aside one
  third of the manuscripts for fitting.
* Abstention rule in both scorings: a completely different title never gives an automatic match,
  unless the reference carries both the record's DOI and its PMID. It stopped a hybrid reference
  being matched with evidence weights, and it does not change the points results on the 800 case
  benchmark.

### Fixed
* Compound surnames written surname first ("San Miguel, J.", "Vasconcelos Cordoba, B.") were
  split into two authors, which raised authors_not_in_record on correct APA references.
* An incomplete reference with an unconfirmed candidate now keeps the reference_incomplete finding.
* Found by the first working live run (800 cases and 123 genuine references):
  * accented capital initials in PubMed records ("Díaz Arias JÁ", "Pérez MÁ", "Pérez-Benito Á")
    were read as part of the surname, so 3 genuine references wrongly got authors_not_in_record;
  * PubMed's "Comment" type was treated as a notice like an erratum, so a correspondence letter
    (PMID 23411466, typed Letter and Comment) was never accepted. That affected 1 genuine
    reference and 3 benchmark cases. Comments are citable; they are now only exempt from counting
    as a rival;
  * the 800 case scorer counted CORRECTED (the right paper, with a published erratum) as a flag;
  * an author list ending "et al," or "et al.," followed by the title ("Smith, J, Jones, K, et al,
    Title") gave no authors and a title carrying the author list. A correct PMID was then left
    unconfirmed (case C16-023), and other references got false "title differs" warnings. RefCheck
    now splits at "et al" when every piece before it looks like a name.
* Volume check: it now runs for every reference RefCheck cannot match, including when PubMed
  returned candidates that were all rejected. The first live run missed 5 of 92 fictional
  references with a common first author (Zhou), which drew loose candidates.
* RIS: group authors from PubMed records (for example "Cancer Genome Atlas Research Network") get
  the trailing comma EndNote reads as a corporate author.
* Every live PubMed lookup failed (CHECK_ERROR) because the hardened XML parser refused any
  DOCTYPE, and every NCBI answer opens with one naming a web DTD. A DOCTYPE that names an
  http or https DTD is now accepted. Inline DTDs, entity declarations and local or relative DTD
  paths are still refused. The mock PubMed in the tests now sends NCBI's exact opening lines, so
  this cannot pass the tests again. Found by the first live run of validation step 1; RefCheck
  reported every reference as not checked rather than verified.
* Word files are checked before parsing: more than 200 MB unpacked or more than 100,000
  paragraphs is refused with a plain message, and empty paragraphs skip the style lookup. A 1 MB
  file holding 3 million empty paragraphs previously took 150 seconds (found by the stress tests).
* The 800 case scorer no longer fails at the end of a live run (it read a counter that only the
  snapshot has); it now honours `REFCHECK_DISABLE` in snapshot mode, takes `NCBI_API_KEY`, and
  has `--no-cache`.
* Duplicate detection no longer merges templated titles that differ only in a gene symbol or
  number ("... linked to ATM expression ..." and "... linked to TP53 expression ...") or in one
  swapped content word ("... in melanoma" and "... in glioblastoma"); 3 of the 100 held out
  references were wrongly reported as duplicates before this change.
* A written DOI is always looked up, also when the PMID or an earlier search already settled
  the match; a DOI of another paper was previously missed in that case.
* An identifier no longer forces an automatic match when the first author and the journal both
  describe another paper (hybrid references).
* Author parsing of hyphenated initials (Lim S-l, Chung T-H) and of a suffix after a comma
  (Shaughnessy JD, Jr); old Wiley SICI DOIs are read whole.
* Secondary evidence (`sources.py`), each optional, cached and audited per destination host:
  Crossref (DOI metadata, publisher and Retraction Watch notices, preprint links, bibliographic
  search for references missing from PubMed), OpenAlex (search, retraction flag, open access
  URL), doi.org registration, NLM Catalog (journal known, currently indexed for MEDLINE), DOAJ
  listing, Unpaywall, and a local Retraction Watch CSV. New findings: `doi_not_registered`,
  `doi_confirmed`, `found_outside_pubmed`, `not_found_in_any_source`, `issn_invalid`,
  `journal_not_in_nlm_catalog`, `journal_not_medline_indexed`, `journal_in_doaj`, `open_access`,
  `reinstated`. Sources that cannot be reached are skipped after two tries and reported.
  Settings `sources` and `retraction_watch_csv` (`REFCHECK_SOURCES`, `REFCHECK_RW_CSV`); command
  line `--sources`, `--no-extra`, `--rw-csv`; sidebar *Extra checks after PubMed*.
  Not tested against the live services (the development environment had no access); tests use
  answers shaped like the documented JSON.
* DOI ownership check (`doicheck.py`): the written DOI's owner (PubMed and Crossref) is compared
  with the cited work on title, first author, year and journal. Linked versions, likely preprint
  versions, notices and reworded titles are reported as such instead of DOI_MISMATCH; likely typos
  name the intended DOI; a DOI of another work combined with a cited work found nowhere is named
  (`doi_other_work_cited_not_found`). Fixed: a retraction recorded on a wrong DOI could mark the
  cited paper as retracted.
* BibTeX (.bib), .bbl and LaTeX `thebibliography` input (`latexbib.py`), no new dependency.
* BibTeX export, and a suggested Vancouver reference text from each matched PubMed record.
* PubMed parsing keeps the journal ISSNs and NLM ID. The network audit records the host of every
  request and summarises by destination.
* Integrity classification with 14 statuses, field level findings and their sources (`verify.py`).
* Detection of retractions, corrections, expressions of concern and preprints from PubMed
  publication types and CommentsCorrections links.
* DOI and PMID identity checks: a written identifier that points to another paper is reported.
* Explicit abstention: an automatic match is refused, with reasons, when the first author or year
  conflicts, the title is dissimilar, a rival is close, or the best record is a notice.
* Online first (electronic) publication years accepted.
* Citation audit (`audit.py`): mapping, citations without a reference, uncited and duplicate
  references, first citation order, numbering gaps, mixed styles.
* Footnotes and endnotes processed; Mendeley and Word citation content controls preserved.
* Round trip validation of the document after EndNote's update (`roundtrip.py`, app step 5,
  `--roundtrip`).
* EndNote XML export; RIS validation after writing; document structure comparison and change report.
* JSON and HTML integrity reports with provenance, thresholds, decisions and network audit.
* Typed NCBI errors, retries with back off, SQLite response cache, offline mode, network audit.
* Configuration by file or environment, including ablation switches.
* Privacy: self deleting workspaces, hosted or local notice, privacy and network panel, delete
  session data, Streamlit usage statistics off, upload limit, safe file names, safe XML parsing.
* Per field parse confidence and parse method.
* Optional local GROBID PDF extraction.
* Command line: batch folders, `--audit-only`, `--offline`, `--no-cache`, `--config`, `--grobid`.
* App: integrity status in review, undo, table search and filter.
* Benchmark tooling: perturbation, grouped splits, runner, metrics with intervals, calibration.
* Docker and compose files; CI (tests on three systems, bandit, pip-audit).
* Documentation in `docs/`.

### Changed
* `classify()` now wraps `decide()`; thresholds come from settings and are labelled provisional.
* Launch scripts create a private environment from `requirements-lock.txt` and bind to localhost.
* pypdf 6.17.0 replaced by 7.1.1 (25 known CVEs in the former).

### Fixed
* Temporary folders with uploaded manuscripts were never deleted (app and LibreOffice conversion).
* `pytest` could not collect the tests (`tests/test_app.py` ran at import).
* Author suffixes (Jr, 2nd, III) became separate authors in RIS.
* GROBID journal title fallback could pick the wrong element.
* IPv6 localhost was treated as hosted; `0.0.0.0` was treated as local.
* Compiled `__pycache__` files removed from the repository.

### Compatibility
* Command line usage from 1.x works unchanged. Output file names are unchanged; new files are added.
* `PubMedClient(email, api_key, log)` still works; `settings`, `cache` and `offline` are optional.

## 1.x

Initial releases: extraction from Word, PDF, text and tables; staged PubMed matching; review
screens; RIS and Excel reports; Word copy with EndNote temporary citations.
