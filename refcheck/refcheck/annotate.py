"""Turn numbered in-text citations in a .docx into EndNote temporary citations.

    "... as shown previously [3,5-7]."  ->  "... as shown previously {Patel, 2022 #3; ...}."

After importing the RIS file into an EndNote library, open the annotated copy in
Word and run EndNote > Update Citations and Bibliography. EndNote replaces each
{Author, Year #Record} with a live citation and builds the bibliography.

Handled citation forms
    [1]  [1,2]  [1-3]  [1, 3-5]       square brackets (default on)
    superscript 1  1,2  1-3            superscript runs (default on)
    (1)  (1,2)  (1-3)                  parentheses (off by default: risky)

Text inside existing Word fields (EndNote/Zotero fields, cross references) is not
touched; those are reported instead.
"""

import re

from docx import Document
from docx.oxml.ns import qn
from docx.text.run import Run

DASH = "‐‑‒–—−-"
NUMLIST = (r"\d{1,4}(?:\s*[" + DASH + r"]\s*\d{1,4})?"
           r"(?:\s*[,;]\s*\d{1,4}(?:\s*[" + DASH + r"]\s*\d{1,4})?)*")
SUP_RE = re.compile(r"\s*(" + NUMLIST + r")\s*")
BRACKET_RE = re.compile(r"\[\s*(" + NUMLIST + r")\s*\]")
PAREN_RE = re.compile(r"\(\s*(" + NUMLIST + r")\s*\)")
# "classification1."  "diagnosis1,2."  "workflows40,44,45"  "astrocytoma6-9)"
GLUED_RE = re.compile(
    r"(?<=[a-z\)\]])(\d{1,3}(?:[" + DASH + r"]\d{1,3})?(?:,\d{1,3}(?:[" + DASH + r"]\d{1,3})?)*)"
    r"(?![\w%°])(?![.,]\d)")
# numbers that are part of an identifier: skipped silently
GLUED_IDENT = {"hg", "mm", "chr", "rs", "v", "ver", "grch", "exon", "intron", "codon", "cd",
               "nm", "np", "ensg", "ensembl", "build", "version", "lane", "sample", "rev"}
# prose words often followed by a number: left unchanged but reported
GLUED_FLAG = {"phase", "grade", "stage", "type", "cycle", "arm", "figure", "fig", "table",
              "section", "step", "class", "group"}
UNIT_BEFORE = re.compile(r"(?:\b10|[x×]\s*10|\b(?:mm|cm|km|m|µm|μm|nm|kg|mg|g|ml|mL|L|s|ft|in))\s*$")
OPAQUE = {qn("w:drawing"), qn("w:pict"), qn("w:object"), qn("w:footnoteReference"),
          qn("w:endnoteReference"), qn("w:sym")}


def glued_ok(text, m):
    """True = citation; False = part of the word, skip silently (IDH1, TP53, CNS5,
    H3K27me3); a string = ambiguous, left unchanged and reported (WHO grade37)."""
    before = text[: m.start()]
    if before.count("{") > before.count("}"):
        return False                      # inside a citation already converted
    w = re.search(r"([A-Za-z]+)$", before)
    if not w:
        return before.endswith((")", "]"))
    word = w.group(1)
    if len(word) < 3 or any(c.isupper() for c in word[1:]) or word.lower() in GLUED_IDENT:
        return False
    if w.start() > 0 and before[w.start() - 1].isdigit():
        return False                      # token such as 1p19q or H3K27me3
    if word.lower() in GLUED_FLAG:
        return f"number attached to '{word}' may be part of the text; left unchanged"
    return True


def expand_numbers(spec):
    out = []
    for part in re.split(r"\s*[,;]\s*", spec.strip()):
        if not part:
            continue
        m = re.match(r"(\d+)\s*[" + DASH + r"]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b < a or b - a > 200:
                return None
            out.extend(range(a, b + 1))
        else:
            out.append(int(part))
    return out


def _is_superscript(run):
    if run.font.superscript:
        return True
    if run.font.superscript is None and run.style is not None:
        st = run.style
        while st is not None:
            if st.font.superscript:
                return True
            if st.font.superscript is False:
                return False
            st = st.base_style
    return False


def _paragraph_runs(p_el, paragraph):
    """Runs of a paragraph in document order, flagging runs that sit inside a
    field (complex or simple) so their text is never rewritten."""
    out, depth, field_kinds = [], 0, []
    for r_el in p_el.iter(qn("w:r")):
        if any(a.tag == qn("w:fldSimple") for a in r_el.iterancestors()):
            out.append((Run(r_el, paragraph), True))
            continue
        for fc in r_el.iter(qn("w:fldChar")):
            t = fc.get(qn("w:fldCharType"))
            if t == "begin":
                depth += 1
            elif t == "end":
                depth = max(0, depth - 1)
        for it in r_el.iter(qn("w:instrText")):
            field_kinds.append((it.text or "").strip())
        out.append((Run(r_el, paragraph), depth > 0 or r_el.find(qn("w:fldChar")) is not None))
    return out, field_kinds


class Annotator:
    def __init__(self, ref_to_rec, rec_keys, n_refs, use_record_numbers=True,
                 brackets=True, superscript=True, parentheses=False, glued=False):
        self.ref_to_rec, self.rec_keys = ref_to_rec, rec_keys
        self.n_refs = n_refs
        self.use_recnum = use_record_numbers
        self.brackets, self.superscript, self.parentheses = brackets, superscript, parentheses
        self.glued = glued
        self.log = []
        self.field_notes = {"endnote": 0, "zotero": 0, "crossref": 0, "other": 0}

    def citation_for(self, nums):
        recs = []
        for n in nums:
            r = self.ref_to_rec.get(n)
            if r is None:
                return None
            if r not in recs:
                recs.append(r)
        parts = []
        for r in recs:
            au, yr = self.rec_keys[r]
            core = f"{au}, {yr}" if yr else au
            parts.append(f"{core} #{r}" if self.use_recnum else core)
        return "{" + "; ".join(parts) + "}"

    # ----------------------------------------------------------------------
    def process_paragraph(self, p_el, paragraph, label):
        runs, kinds = _paragraph_runs(p_el, paragraph)
        for k in kinds:
            ku = k.upper()
            if "EN.CITE" in ku:
                self.field_notes["endnote"] += 1
            elif "ZOTERO" in ku or "CSL_CITATION" in ku:
                self.field_notes["zotero"] += 1
            elif ku.startswith("REF ") or ku.startswith("NOTEREF"):
                self.field_notes["crossref"] += 1
            elif ku:
                self.field_notes["other"] += 1
        usable = [(r, locked or any(c.tag in OPAQUE for c in r._r)) for r, locked in runs]
        if self.superscript:
            self._superscripts(usable, label)
        if self.brackets:
            self._pattern(usable, BRACKET_RE, label, "brackets")
        if self.parentheses:
            self._pattern(usable, PAREN_RE, label, "parentheses")
        if self.glued:
            self._pattern(usable, GLUED_RE, label, "glued", accept=glued_ok)

    def _context(self, usable, idx_from, idx_to):
        before = "".join(r.text for r, _ in usable[max(0, idx_from - 6):idx_from])[-60:]
        after = "".join(r.text for r, _ in usable[idx_to + 1: idx_to + 6])[:40]
        return before, after

    def _superscripts(self, usable, label):
        i = 0
        while i < len(usable):
            r, locked = usable[i]
            if locked or not r.text or not _is_superscript(r):
                i += 1
                continue
            j = i
            while j + 1 < len(usable) and not usable[j + 1][1] and (
                    _is_superscript(usable[j + 1][0]) or usable[j + 1][0].text == ""):
                j += 1
            group = [usable[k][0] for k in range(i, j + 1)]
            text = "".join(g.text for g in group)
            m = SUP_RE.fullmatch(text)
            before, after = self._context(usable, i, j)
            if m:
                nums = expand_numbers(m.group(1))
                problem = ""
                if UNIT_BEFORE.search(before) or after[:1] in "+-−":
                    problem = "looks like an exponent or unit; left unchanged"
                elif not nums or max(nums) > self.n_refs or min(nums) < 1:
                    problem = "number outside the reference list; left unchanged"
                cit = None if problem else self.citation_for(nums)
                if cit is None and not problem:
                    problem = "reference number not found; left unchanged"
                if cit:
                    group[0].text = cit
                    group[0].font.superscript = False
                    for g in group[1:]:
                        if g.text:
                            g.text = ""
                self.log.append(dict(paragraph=label, original=text, replaced_with=cit or "",
                                     refs=",".join(map(str, nums or [])), problem=problem,
                                     context=(before + "░" + text + "░" + after).strip()))
            i = j + 1

    def _pattern(self, usable, regex, label, kind, accept=None):
        segs = []                      # (run, start, end) for editable runs
        full, pos = [], 0
        for r, locked in usable:
            t = "" if locked else r.text
            # a locked run breaks the text so matches never span it
            if locked:
                full.append("\x00")
                pos += 1
                continue
            segs.append((r, pos, pos + len(t)))
            full.append(t)
            pos += len(t)
        text = "".join(full)
        matches = list(regex.finditer(text))
        for m in reversed(matches):
            verdict = accept(text, m) if accept else True
            if verdict is False:
                continue
            nums = expand_numbers(m.group(1))
            ctx = (text[max(0, m.start() - 60):m.start()] + "░" + m.group(0) + "░" +
                   text[m.end():m.end() + 40]).replace("\x00", "")
            problem = verdict if isinstance(verdict, str) else ""
            if not problem and (not nums or max(nums) > self.n_refs or min(nums) < 1):
                if kind == "glued":
                    continue              # e.g. 'week2024': not a citation at all
                problem = "number outside the reference list; left unchanged"
            cit = None if problem else self.citation_for(nums)
            if cit is None and not problem:
                problem = "reference number not found; left unchanged"
            if cit:
                self._replace_span(segs, m.start(), m.end(), cit)
            self.log.append(dict(paragraph=label, original=m.group(0), replaced_with=cit or "",
                                 refs=",".join(map(str, nums or [])), problem=problem, context=ctx))

    @staticmethod
    def _replace_span(segs, s, e, new):
        hit = [(r, a, b) for r, a, b in segs if b > s and a < e]
        if not hit:
            return
        first, a0, _ = hit[0]
        last, al, _ = hit[-1]
        if first is last:
            t = first.text
            first.text = t[: s - a0] + new + t[e - a0:]
            return
        tail = last.text[e - al:]
        first.text = first.text[: s - a0] + new
        for r, _, _ in hit[1:-1]:
            r.text = ""
        last.text = tail


def annotate_docx(src, dst, ref_to_rec, rec_keys, heading_para=None, reflist_end_para=None,
                  remove_reference_list=True, **opts):
    """Write an annotated copy of `src` to `dst`. Returns (citation_log, warnings)."""
    doc = Document(str(src))
    body = doc.element.body
    top = list(doc.paragraphs)
    skip = set()
    if heading_para is not None:
        end = reflist_end_para if reflist_end_para is not None else heading_para
        skip = {top[i]._p for i in range(heading_para, min(end, len(top) - 1) + 1)}

    ann = Annotator(ref_to_rec, rec_keys, n_refs=max(ref_to_rec) if ref_to_rec else 0, **opts)
    para_no = 0
    for el in body.iterchildren():
        if el.tag == qn("w:p"):
            para_no += 1
            if el in skip:
                continue
            ann.process_paragraph(el, _wrap(el, doc), f"P{para_no}")
        elif el.tag == qn("w:tbl"):
            for k, p_el in enumerate(el.iter(qn("w:p")), 1):
                ann.process_paragraph(p_el, _wrap(p_el, doc), f"table near P{para_no}, cell para {k}")

    if remove_reference_list and heading_para is not None and reflist_end_para is not None:
        for i in range(heading_para + 1, min(reflist_end_para, len(top) - 1) + 1):
            p = top[i]._p
            p.getparent().remove(p)

    doc.save(str(dst))

    warnings = []
    fn = ann.field_notes
    if fn["endnote"] or fn["zotero"]:
        warnings.append(
            f"The document already contains {fn['endnote']} EndNote and {fn['zotero']} Zotero citation "
            "fields. Those were left as they are; only plain text citations were converted.")
    if fn["crossref"]:
        warnings.append(
            f"{fn['crossref']} citations are Word cross reference fields and were not converted. "
            "To include them, select those citations in Word and press Ctrl+Shift+F9 to turn them into "
            "plain text (this unlinks every field in the selection), then run again.")
    bad = [e for e in ann.log if e["problem"]]
    if bad:
        warnings.append(f"{len(bad)} possible citations were left unchanged; see the In-text citations sheet.")
    return ann.log, warnings


def _wrap(p_el, doc):
    from docx.text.paragraph import Paragraph
    return Paragraph(p_el, doc._body)


def detect_citation_forms(src, heading_para=None, n_refs=999):
    """Count candidate citations of each form in the body (before the reference
    list) so the app can preset the options. Returns a dict of counts."""
    doc = Document(str(src))
    top = list(doc.paragraphs)
    stop = top[heading_para]._p if heading_para is not None and heading_para < len(top) else None
    counts = {"brackets": 0, "superscript": 0, "parentheses": 0, "glued": 0}
    for el in doc.element.body.iterchildren():
        if el is stop:
            break
        paras = [el] if el.tag == qn("w:p") else list(el.iter(qn("w:p"))) if el.tag == qn("w:tbl") else []
        for p_el in paras:
            para = _wrap(p_el, doc)
            runs = [r for r, locked in _paragraph_runs(p_el, para)[0] if not locked]
            text = "".join(r.text for r in runs)
            ok = lambda m: (lambda n: bool(n) and 1 <= min(n) and max(n) <= n_refs)(expand_numbers(m.group(1)))
            counts["brackets"] += sum(1 for m in BRACKET_RE.finditer(text) if ok(m))
            counts["parentheses"] += sum(1 for m in PAREN_RE.finditer(text) if ok(m))
            counts["glued"] += sum(1 for m in GLUED_RE.finditer(text) if glued_ok(text, m) is True and ok(m))
            counts["superscript"] += sum(1 for r in runs if r.text.strip() and _is_superscript(r)
                                         and SUP_RE.fullmatch(r.text))
    return counts


def suggested_options(counts):
    """Brackets and superscripts are always safe to convert. Parentheses and
    numbers stuck to words are switched on only when they clearly dominate."""
    safe = counts["brackets"] + counts["superscript"]
    return dict(brackets=True, superscript=True,
                parentheses=counts["parentheses"] >= 5 and counts["parentheses"] > 2 * safe,
                glued=counts["glued"] >= 5 and counts["glued"] > safe)
