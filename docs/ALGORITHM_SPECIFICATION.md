# Algorithm specification

Version: match 2.0, verify 2.0, audit 1.0 (`refcheck.report.ALGORITHM_VERSION`).
All numbers below are the values in the code. Weights and thresholds are design choices that the
benchmark is meant to test. None is an empirical probability.

## 1. Extraction

**Section detection.** The reference section starts after the last heading matching *References,
Bibliography, Literature cited, Works cited, Reference list* (or a user supplied heading) that is
followed by the longest run of numbered items. Reading stops at a stop heading (figure legends,
tables, supplementary, acknowledgements, funding, conflicts, declarations, author contributions,
data availability, abbreviations, publisher's note, author information, rights and permissions).
A new item starts at a typed number (`1.`, `1)`, `[1]`) that is 1 to 3 above the previous one, or at
a Word automatic numbered paragraph. A wrapped DOI line such as `10.3389/...` is not a number.

**PDF clean up.** Manuscript line numbers are removed only when at least 8 of the next 24 lines
continue an exact sequence, so ordinary trailing numbers survive. Lines of up to 60 characters
repeated three or more times (running headers, watermarks) are dropped.

**Parsing.** The year anchor is searched in this order, and the first style that matches wins:

| Method | Example ending | Confidence base |
|---|---|---|
| `endnote_country_year` | `Sci Rep. 15. England(2025). p. 2204.` | high |
| `vancouver` | `Neuro Oncol. 2025;27(8):2035-2046.` (year after `. `) | high |
| `paren_year` | `Semin Oncol (2016) 43(6):676-81.` | medium |
| `apa` | `Smith, J. (2020). Title. Journal, 12(3), 45-67.` | medium |
| `loose_year` | `J Clin Oncol 2025;40:1-10.` | medium |
| `fallback` | anything else | low |

The text before the anchor is split at sentence boundaries that are not author initials: first
boundary ends the authors, last boundary starts the journal. DOI and PMID are taken from anywhere
in the reference. Per field confidence starts from the method's base level and is lowered for
implausible values (titles under 20 or over 400 characters, journal names over 120 characters,
authors without a capitalised surname). DOIs and four digit years are raised to high. These labels
steer the user's attention; their accuracy is measured in the benchmark.

## 2. Resolution against PubMed

**Search plan** (`pubmed._plan`), most specific first. Each step can be switched off for ablation
(`Settings.disabled`).

1. `pmid-in-text`: EFetch the PMID written in the reference
2. `doi`: ESearch `"doi"[aid]`
3. `citmatch`: ECitMatch `journal|year|volume|first page|first author`
4. `journal-vol-page`: `"journal"[ta] AND year[dp] AND volume[vi] AND page[pg]`
5. `author-year-page`: `surname[1au] AND year[dp] AND page[pg]`
6. `title-phrase`: `"title"[Title]`
7. `title+author`, `title+year` (year plus or minus one), `title-words`: every word tagged `[Title]`.
   Words with digits (superscript risk) or British and American spelling variants are left out.
8. `title-words-loose`: the six longest title words

After each step new candidates are fetched (EFetch, batched to 150) and every candidate is scored.
The search stops early when `decide` returns AUTO_MATCH (`early-stop`). At most 40 candidates are
considered per reference.

**Two scorings.** `Settings.scoring` selects `points` (default, the hand set values of RefCheck
1.x below) or `evidence` (weights estimated from labelled data, next paragraph). Both use the
same field comparisons. `evidence.compare_fields` records one level per field for every
candidate retrieved, in both scorings, and `points_from_levels` rebuilds the points from those
levels exactly. This is tested on every candidate of the 800 case benchmark, so the two
scorings can be compared on identical candidates.

**Score, points** (`score_candidate`). Title similarity `ts` is `difflib.SequenceMatcher` on normalised
titles (NFKD, lower case, punctuation removed, British spellings mapped). `to` is word Jaccard.

| Evidence | Points |
|---|---|
| ts ≥ 0.98 / ≥ 0.94 / ≥ 0.88 / ≥ 0.80 | 60 / 54 / 46 / 34 |
| else word overlap ≥ 0.75 / ≥ 0.60 | 30 / 20 |
| else DOI or written PMID identical | 10 (otherwise the candidate is dropped) |
| the reference has no title (`Mahon FX, et al. Lancet Oncol. 2010;11:1029-35.`, a DOI or a PMID alone) | 46, or 56 for a PMID written alone (reason `no-title`; the title abstentions do not apply) |
| first author surname identical / first five letters, one typing slip ('Slmaon'), or the reference starts at PubMed's second author | +15 / +8 (otherwise reason `author-differs`) |
| year equal to print or online first year / off by one / more | +10 / +2 / −12 |
| journal identical (full or ISO abbreviation) / one contains the other | +8 / +5 |
| volume identical | +3 |
| first page identical (supplement prefixes kept, e.g. `iv1`) | +5 |
| DOI identical after normalisation | +25 |
| written PMID identical | +30 |

DOI normalisation: lower case, strip `https://doi.org/`, `dx.doi.org/`, `doi:`, trailing punctuation.

The points have no evidential basis: they were chosen by hand. For a reference without a title the
base of 46 is chosen so that the automatic threshold (86) is reached only when every field the
reference gives agrees: first author, journal, year, volume and first page (46+15+10+8+3+5 = 87),
or first author and DOI (46+15+25 = 86); a PMID written alone gives 56+30 = 86. A title cut short
(at least five words that open the record's title and at least 40% of it) counts as ts 0.88.

**Score, evidence** (`evidence.py`, weights fitted on labelled references, Fellegi and Sunter
1969). Levels per field:

* title: t98, t94, t88, t80 (similarity bands as above), w75, w60 (word overlap bands when
  similarity is below 0.80), low;
* first author: exact, prefix5, differs;
* year: same, online first, one apart, more apart;
* journal: exact, partial, differs;
* volume, first page, DOI and written PMID: agree, differs;
* every field: missing.

For each level, m = P(level | right paper) and u = P(level | wrong paper returned by the same
searches) are estimated among comparisons where the field is present, with additive smoothing
0.5. The level's weight is log2(m / u), and a missing field weighs 0. A field with fewer than 20
present comparisons among right or among wrong candidates is not estimated: all its weights are
0. The candidate's log2 odds is the prior log2 odds (right to wrong candidates in the training
data) plus its weights. The probability is p = 1 / (1 + exp(−(a · log2 odds + b))), with a and
b fitted by logistic regression on the training pairs. Weight intervals are 95% percentile
bootstraps over references.

The estimates use labelled live runs only. m comes from natural citations (the user's genuine
references and the development third of the real manuscripts). u comes from those sources plus
hard cases. The 800 case benchmark is never used for fitting, because its errors were designed.
References are split by a hash of their identifier: 70% to fit and 30% held out. The held out
part reports the Brier score, expected calibration error, a reliability table, the AUC of
right against wrong candidates, and automatic matches, precision, wrong PMIDs and coverage for
both scorings. The weights file records the weights, intervals, m, u, counts, prior,
calibration, thresholds and the data it was fitted on.

**Decision with abstention** (`decide`). Points: provisional thresholds `auto_score = 86`,
`review_score = 70`, `min_gap = 5` (RefCheck 1.x values, recorded as `threshold_source`).
Evidence: probability thresholds from the weights file, by default automatic at p ≥ 0.99 with a
lead of at least 3.32 bits (10 times the odds) over the runner up, and review at p ≥ 0.80.

* above the automatic threshold gives AUTO_MATCH **unless** any of these hold, in which case REVIEW:
  * runner up within `min_gap` points. A runner up that is a notice (erratum, retraction notice,
    expression of concern, comment) about the same work is not counted as a rival.
  * without DOI or PMID evidence: first author differs, year differs by more than one, or title
    similarity below 0.80
  * with DOI or PMID evidence: the first author and the journal both differ (hybrid reference)
  * the title differs completely (level low) although other fields agree, unless the reference
    carries both the record's DOI and its PMID. Journal, volume, page and DOI come from one record
    and are not independent evidence. With both identifiers typed by the author, a low title
    usually means the title was misread from an unusual layout.
  * the best candidate is itself a notice
* `review_score` ≤ score < `auto_score` gives REVIEW
* otherwise NO_MATCH, or ERROR if every search failed with an NCBI error

Each REVIEW from the vetoes carries its reasons (`abstain`), shown to the user and written to the
reports. The design preference is an unresolved reference over a wrong automatic match.

**Calibration.** The calibration script of the validation package picks the lowest score whose accepted set reaches a
precision Wilson lower bound of 0.99 on the development split, and writes a configuration file.
Platt scaling maps scores to probabilities for evaluation only. The app shows a probability only
after one has been fitted and validated on held out data. Until then it shows the score and says
it is not a probability.

**Client behaviour.** Requests are spaced by 0.35 s (0.11 s with an API key). HTTP 429 and 5xx
responses and connection errors are tried up to four times in all, with exponential back off
between attempts (1.5 s, 3 s, 6 s).
HTTP 4xx is not retried. Errors are typed: `timeout`, `connection`, `rate_limited`,
`server_error`, `bad_response`, `offline_cache_miss`. Responses are cached in memory for the
session and, unless disabled, in a SQLite file keyed by endpoint and query (never the email or key)
with a 30 day lifetime. Offline mode answers only from that cache.

## 3. Verification

Inputs: the parsed reference, the match result, the final PMID (automatic or the user's), and the
PubMed records for that PMID and for whatever the written DOI or PMID points to.

Findings (each with `code, severity, detail, source`):

| Code | Severity | Rule |
|---|---|---|
| `title_differs` | major below 0.80, minor below 0.94 | title similarity |
| `first_author_differs` | major | surnames differ and the first five letters differ |
| `year_off_by_one` | minor | closest of print or online year is one off |
| `year_differs` | major | more than one off |
| `journal_differs` | major | neither full title nor ISO abbreviation matches or contains the other |
| `volume_differs` | major | |
| `issue_differs` | minor | |
| `pages_differ` | major | first page differs |
| `last_page_differs` | minor | first page agrees, last page differs (page ranges such as 1029-35 written out) |
| `doi_mismatch` | major | the written DOI belongs to another work (section 3.2) |
| `doi_differs` | major | written DOI differs from PubMed's DOI and neither PubMed nor Crossref knows it |
| `pmid_mismatch` | major | written PMID is another paper or does not exist |
| `retracted` | critical | publication type *Retracted Publication* or a `RetractionIn` link |
| `is_retraction_notice` | critical | the record is itself a retraction notice |
| `expression_of_concern` | critical | `ExpressionOfConcernIn` link |
| `corrected` | info | `ErratumIn` or `CorrectedandRepublishedIn` link |
| `preprint` | info | publication type *Preprint*, or for unmatched references a preprint server or DOI prefix |
| `published_version` | info | preprint with an `UpdateIn` link |
| `duplicate` | major | same PMID as an earlier reference, or the same title with the same year: normalised title similarity ≥ 0.95, the same distinctive tokens (words with a digit, and in mixed case titles words of two or more capitals, such as TP53, ATM, IDH2), and every differing word a spelling variant (similarity ≥ 0.75) of a word in the other title |
| `title_repeated` | minor | the same title (as for `duplicate`) as an earlier reference but a different year, and the two are not resolved to different PMIDs |

Primary status, when a PMID is final, is the first that applies in this order: RETRACTED,
EXPRESSION_OF_CONCERN, PMID_MISMATCH, DOI_MISMATCH, DUPLICATE, METADATA_ERROR (any major field
finding), CORRECTED, PREPRINT, VERIFIED_WITH_WARNINGS (only minor findings), VERIFIED. All that
apply are kept as `flags`.

Without a final PMID: CHECK_ERROR if the search failed; UNRESOLVED if a candidate exists but was
not confirmed (abstention, or below the automatic threshold) and the user has not decided;
otherwise PREPRINT, NON_PUBMED_SOURCE (software or repository, book or chapter, thesis,
conference material, report or guideline, web page, no journal) or NOT_FOUND_IN_PUBMED.
No status means fabricated. A benchmark may define "potentially fabricated" only through its own
validated procedure.

### 3.0 Word, author and completeness checks on a matched record

| Code | Severity | Rule |
|---|---|---|
| `title_wording_differs` | minor | title similarity at least 0.94, but the content words differ after removing stop words and folding British and American spellings and Greek letters; the detail lists the differing words |
| `title_word_order_differs` | minor | the same words in a different order |
| `first_author_initials_differ` | minor | same first author surname, initials neither equal nor a prefix of each other |
| `authors_not_in_record` | major | a listed author after the first is not among the PubMed authors (only when PubMed lists at least as many authors) |
| `author_order_differs` | minor | all listed authors are in PubMed, in a different order |
| `authors_omitted` | major | the listed authors start after the first PubMed author |
| `pmid_not_found` | major | the written PMID returns no PubMed record (not counted as PMID_MISMATCH) |
| `reference_incomplete` | minor on a matched record, major when unmatched (status UNRESOLVED) | authors, title, journal or year missing |
| `doi_not_in_pubmed` | minor | unmatched reference whose written DOI has no PubMed record |
| `volume_not_in_pubmed` | major | unmatched reference with a numeric volume: PubMed holds records of the cited journal (`[ta]`) within a year either side of the cited year, but none in the cited volume (`[vi]`). Not run, and silent, when the journal has no records in that window (journal not in PubMed, not yet published, or an abbreviation PubMed does not recognise). Two extra searches per unmatched reference; disable with `REFCHECK_DISABLE=volume-check`. The status stays NOT_FOUND_IN_PUBMED |

The written DOI is searched (`[aid]`) even when an earlier search already produced a confident
match. A candidate supported by an identifier is not accepted automatically when its first
author and journal both differ from the reference.

### 3.1 Secondary sources (optional, `sources.py`)

Run after PubMed resolution, when building the outputs, for the sources enabled in
`settings.sources`. Each answer is cached and recorded in the network audit with its host.
A lookup that fails adds nothing and is listed in the warnings; a source that fails twice with a
connection error, or twice with HTTP 429 or 5xx, is skipped for the rest of the session.

Retraction Watch copy (`rwdata.py`, source `retractionwatch`): before the run, a HEAD request to
the GitLab repository files API for `retraction_watch.csv` on `main` returns
`X-Gitlab-Content-Sha256` and `X-Gitlab-Blob-Id`. Equal to the local copy's SHA-256 (or, without
a checksum, its stored blob id): no download. Otherwise the raw file is streamed to a temporary
name and accepted only if its SHA-256 equals the advertised one, it has the columns Record ID,
OriginalPaperDOI, OriginalPaperPubMedID, RetractionNature and RetractionDate, and it holds at
least 90% of the previous copy's rows; then it replaces the copy atomically. Without metadata, a
conditional GET (`If-None-Match` with the stored ETag) is used, and a download identical to the
local copy is discarded. A local copy whose checksum no longer matches the stored state is
refreshed. Failures keep the previous copy (status `stale`); offline mode makes no request
(`offline`). A user supplied `retraction_watch_csv` is used as is and never updated.

| Code | Severity | Source | Rule |
|---|---|---|---|
| `retracted` | critical | Crossref | `update-to` entry of type retraction, withdrawal, removal or partial retraction with `source = retraction-watch`; or a publisher entry on a record whose title does not look like a notice; or any `updated-by` entry of those types |
| `expression_of_concern` | critical | Crossref, Retraction Watch | as above, type expression of concern; or a Retraction Watch row whose nature contains "concern" |
| `corrected` | info | Crossref, Retraction Watch | as above, type correction, erratum, corrigendum, addendum, clarification, new version |
| `retracted` | critical | OpenAlex | `is_retracted = true` on the record found by DOI or by title |
| `retracted` | critical | Retraction Watch CSV | a row whose `OriginalPaperDOI` (the written DOI, or the matched record's) or `OriginalPaperPubMedID` matches, nature Retraction; or a concern or correction row whose Reason contains "Updated to Retraction". When neither identifier finds a row: the same normalised title (at least 30 characters) and the reference's first author surname among that row's author names (any word, or a run of final words, so "Newsome-Cuby" and "Al-Hamadani" match), year within one when both are known; the finding source then says "matched by title and first author" |
| `reinstated` | info | Retraction Watch CSV | nature "Reinstatement"; also replaces a retraction or concern row dated on or before the latest reinstatement of the same paper (the notice was superseded) |
| `doi_not_registered` | major | doi.org | PubMed and Crossref do not know the DOI and the handle API answers not found (response code 100 or HTTP 404) |
| `found_outside_pubmed` | info | Crossref, OpenAlex | unmatched reference; the best hit of a bibliographic search has title similarity ≥ 0.90 and a year within one |
| `not_found_in_any_source` | major (if status NOT_FOUND_IN_PUBMED), else info | all searched | unmatched reference, no hit as above, and at least one of Crossref and OpenAlex answered. Only sources that answered are named |
| `published_version` | info | Crossref | `relation.is-preprint-of` DOIs |
| `open_access` | info | Unpaywall, OpenAlex | `best_oa_location.url`, else OpenAlex `open_access.oa_url` |
| `issn_invalid` | major | local | an ISSN written after "ISSN" in the reference, or in the PubMed record, fails the ISO 3297 mod 11 check digit |
| `journal_not_in_nlm_catalog` | minor (unmatched), info (matched) | NLM Catalog | no record for `"ISSN"[is]` (valid ISSNs) or else `"abbreviation"[ta]` |
| `journal_not_medline_indexed` | info | NLM Catalog | found, but not with `AND currentlyindexed` |
| `journal_in_doaj` | info | DOAJ | a valid ISSN is listed |

Status effects: `retracted` adds RETRACTED and `expression_of_concern` adds EXPRESSION_OF_CONCERN
for matched and unmatched references; `corrected` adds CORRECTED, and `doi_not_registered` and
`issn_invalid` add METADATA_ERROR, for matched references only; `doi_mismatch` adds DOI_MISMATCH.
For an unmatched reference, `found_outside_pubmed` replaces NOT_FOUND_IN_PUBMED by PREPRINT (Crossref
type `posted-content` or OpenAlex type `preprint`) or NON_PUBMED_SOURCE. The primary status then
follows the precedence above whenever a matched record, RETRACTED, EXPRESSION_OF_CONCERN,
PMID_MISMATCH, DOI_MISMATCH or DUPLICATE is present. Notices found on the written DOI of an
unmatched reference are ignored when that DOI belongs to another work.

### 3.2 Does the written DOI belong to the cited work? (`doicheck.py`)

The written DOI is looked up on its own: PubMed `"doi"[aid]` and, when enabled, Crossref
`/works/{doi}`. Each record found is an *owner*. The *claimed work* is the matched PubMed record,
or for an unmatched reference the written fields. Four fields are compared:

| Field | Agrees when |
|---|---|
| title | similarity ≥ 0.80, or one title is the start of the other and at least 30 characters long |
| first author | surnames equal, share the first five letters, or one contains the other |
| year | any print or online year within one |
| journal | an ISSN in common, or one normalised name equals or contains the other |

Verdict: **same** when the title agrees. **Reworded** when the title disagrees but its similarity
is at least 0.50, at least two other fields agree and none disagrees. **Different** when the title
disagrees otherwise, or (no title) when two fields disagree. A different title with the same
author, journal and year stays *different*, because research groups publish companion papers.
Unknown fields neither agree nor disagree.

Before *different* becomes `doi_mismatch`, in this order:

| Code | Severity | Rule |
|---|---|---|
| `doi_is_notice` | minor | the owner is a notice (PubMed type Published Erratum, Retraction of Publication, Expression of Concern; or a title starting "Correction to:", "Erratum:", "Retraction Note:" and similar; or a Crossref publisher `update-to` on a notice) and it points to the claimed work (DOI or PMID link, or the title after the notice prefix agrees). Checked first, because a notice title contains the article title |
| `doi_is_related_version` | info | Crossref `relation` links the owner and the claimed work (any relation type), or PubMed CommentsCorrections links the two PMIDs |
| `doi_is_related_version` | minor | no link, but one side is a preprint (Crossref `posted-content`, PubMed type Preprint, or a preprint DOI prefix such as 10.1101 or 10.21203), the first author agrees and the years are within two |
| `doi_owner_unclear` | minor | too few fields to compare |
| `doi_mismatch` | major, status DOI_MISMATCH | otherwise; the detail names the owner and the fields that differ |

Other findings: `doi_confirmed` (info, unmatched, verdict same), `doi_alternative` (info,
matched, a second DOI registered for the same work), `doi_title_differs` (minor, reworded),
`doi_typo_likely` (info, beside a mismatch or `doi_differs`: the intended DOI is known, has the
same prefix and differs by at most two characters, Levenshtein distance).

Notices recorded on the written DOI (Crossref `update-to`) are applied to the reference only
when the verdict is same or reworded, so the retraction of another work never marks the cited
work as retracted.

**The fabrication pattern.** For an unmatched reference with `doi_mismatch`, when the cited work
was also not found by the title searches (`not_found_in_any_source`, or with PubMed only the
`not_found` finding), RefCheck adds `doi_other_work_cited_not_found` (major): "the DOI belongs to
another work and the cited work was not found in <sources>". The status stays DOI_MISMATCH. The
finding describes the evidence; it does not say the reference is fabricated.

The suggested reference text (`suggested_reference`) is built from the PubMed record in NLM
(Vancouver) style: up to six authors then "et al", title, ISO abbreviation without periods,
year;volume(issue):pages, DOI.

## 4. Citation audit

**Recognition** (`annotate.Annotator`), in body paragraphs, table cells, paragraphs after the
reference list (legends), footnotes and endnotes:

| Form | Pattern | Default |
|---|---|---|
| brackets | `[1]`, `[1,2]`, `[1-3]`, `[1, 3-5]` | on |
| superscript | runs formatted superscript, directly or through the style chain, containing only a number list | on |
| parentheses | `(1,2)` | on when ≥ 5 found and more than twice the safe forms |
| attached | `classification1,2.` | on when ≥ 5 found and more than the safe forms |

Exclusions: runs inside any field (EndNote, Zotero, Mendeley, Word citations, cross references)
or a citation content control; superscripts after `10`, `×10` or a unit, or followed by `+` or `−`;
for attached numbers, words of under 3 letters, words with internal capitals (gene symbols such as
TP53, IDH1, CNS5), identifier words (hg, chr, rs, exon, build, version and others), words preceded
by a digit (H3K27me3, 1p19q). Numbers attached to *grade, stage, phase, type, figure, table* and
similar are ambiguous: left unchanged and reported. Ranges over 200 or reversed are invalid.
Numbers outside 1..N are reported as citations without a reference.

**Reconciliation** (`audit.citation_audit`): citations are put in document order (paragraph
order, then character offset). Reported: mapping of every converted citation to reference numbers,
citations without a reference, references never cited, duplicates, references first cited out of
numerical order (a reference is flagged when a lower numbered one is first cited after it), gaps
in the numbering, and the mix of citation forms.

## 5. Word and EndNote

**Records.** One record per distinct PMID. Unmatched references become their own record. RIS fields:
`TY, AU (Last, First[, suffix]), TI, T2, J2, PY, VL, IS, SP, EP` (MEDLINE abbreviated end pages
expanded), `DO, AN (PMID), C2 (PMCID), UR, DB, LA, AB, KW, LB (manuscript numbers), N1 (status
and original text)`. The RIS file is parsed back and validated after writing. EndNote XML carries
the same content with `label` and `accession-num` fields.

**Temporary citations.** A citation of references *a, b* becomes `{Author, Year #Ra; Author, Year #Rb}`
where `R` is the record's position in the RIS file plus `record_start − 1`. This equals EndNote's
record number only when the file is imported into an empty library, which is why that is the
documented route. Without record numbers the citation is `{Author, Year}` and EndNote asks when
several records fit. RefCheck never edits an EndNote library and never runs EndNote. The Word copy
contains plain text until the user runs *Update Citations and Bibliography*.

**Structure check.** Paragraph, table, image, section, footnote, endnote, equation and field
counts of the original and the copy are compared. The expected paragraph difference is the number
of reference list paragraphs removed.

**Round trip** (`roundtrip.validate_roundtrip`). In the document saved after EndNote's update:
complex fields with `ADDIN EN.CITE` (inline XML) or `EN.CITE.DATA` (base64 field data) are read in
body, footnotes and endnotes. Each `Cite` is mapped to manuscript numbers by its record Label,
then by PMID (`accession-num`), then by title similarity ≥ 0.9. The multiset of cited records is
compared with the multiset RefCheck converted (by record, so duplicates collapse). Fields already
present in the original are allowed for. Leftover temporary citations, missing, unexpected and
unidentifiable citations are reported. The check is OK only if all four lists are empty.

## 6. Cost

Per reference the number of NCBI requests is between 2 (written DOI, immediate match) and about
20 (no match, every step tried). The audit records the exact number per run.
