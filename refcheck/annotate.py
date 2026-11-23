"""Turn numbered in text citations in a .docx into EndNote temporary citations.

    "... as shown previously [3,5-7]."  ->  "... as shown previously {Patel, 2022 #3; ...}."

After importing the RIS file into an EndNote library, open the annotated copy in
Word and run EndNote > Update Citations and Bibliography. EndNote replaces each
{Author, Year #Record} with a live citation and builds the bibliography.

Handled citation forms
    [1]  [1,2]  [1-3]  [1, 3-5]       square brackets (default on)
    superscript 1  1,2  1-3            superscript runs (default on)
    (1)  (1,2)  (1-3)                  parentheses (off by default: risky)

Body text, tables, figure legends, footnotes and endnotes are processed. Text inside
existing Word fields and citation content controls (EndNote, Zotero, Mendeley, Word's
own citations, cross references) is never touched; those are counted and reported.
"""

import re

from docx import Document
from docx.oxml.ns import qn
from docx.text.run import Run

DASH = "‐‑‒–—−-"
NUMLIST = (r"\d{1,4}(?:\s*[" + DASH + r"]\s*\d{1,4})?"
           r"(?:\s*[,;]\s*\d{1,4}(?:\s*[" + DASH + r"]\s*\d{1,4})?)*")
SUP_RE = re.compile(r"\s*(" + NUMLIST + r")\s*")
# a superscript run that also holds brackets or the punctuation after it: '[1]', '(8,9)', '2,', '13.'
SUP_WRAPPED_RE = re.compile(r"\s*[\[(]?\s*(" + NUMLIST + r")\s*[\])]?\s*([.,;:]?)\s*")
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
              "section", "step", "class", "group", "week", "weeks", "day", "days", "month", "months",
              "year", "years", "visit", "dose", "level", "cohort", "patient", "sample", "case"}
# two letter words a citation may follow ('in long follow up7'); other short words are units or names
GLUED_SHORT_OK = {"up", "in", "on", "of", "to", "by", "as", "at", "is", "it", "be", "we", "or", "so"}
# IEEE ranges: '[4]–[6]' cites 4, 5 and 6
IEEE_RANGE_RE = re.compile(r"\[\s*(\d{1,4})\s*\]\s*[" + DASH + r"]\s*\[\s*(\d{1,4})\s*\]")
UNIT_BEFORE = re.compile(r"(?:\b10|[x×]\s*10|\b(?:mm|cm|km|m|µm|μm|nm|kg|mg|g|ml|mL|L|s|ft|in))\s*$")
# a one letter symbol straight before the superscript: R2, I2, r2, chi2 (Greek) are powers, not citations
SYMBOL_BEFORE = re.compile(r"(?:^|[\s(\[;,=])[A-Za-z\u0370-\u03ff]$")
OPAQUE = {qn("w:drawing"), qn("w:pict"), qn("w:object"), qn("w:footnoteReference"),
          qn("w:endnoteReference"), qn("w:footnoteRef"), qn("w:endnoteRef"), qn("w:sym")}
FIELD_KINDS = ("endnote", "zotero", "mendeley", "word_citation", "crossref", "other")


def field_kind(instr):
    """Classify a field instruction or content control tag."""
    ku = (instr or "").strip().upper()
    if not ku:
        return None
    if "EN.CITE" in ku or "EN.REFLIST" in ku:
        return "endnote"
    if "ZOTERO" in ku:
        return "zotero"
    if "MENDELEY" in ku or "CSL_CITATION" in ku:
        return "mendeley"
    if ku.startswith("CITATION") or ku.startswith("BIBLIOGRAPHY"):
        return "word_citation"
    if ku.startswith("REF ") or ku.startswith("NOTEREF"):
        return "crossref"
    return "other"


def existing_citations(p_el):
    """(kind, shown text) for each reference manager citation already in the paragraph: an EndNote,
    Zotero, Mendeley or Word citation field or content control. Nested fields (EN.CITE.DATA) belong
    to the field around them."""
    out, stack = [], []          # stack: [kind, in_result, text, position] per open complex field
    seen, pos = set(), 0         # pos: characters of plain text before, as the converter counts them
    for r_el in p_el.iter(qn("w:r")):
        if any(a.tag == qn("w:txbxContent") for a in _ancestors_until(r_el, p_el)):
            continue
        sdt = next((a for a in r_el.iterancestors() if a.tag == qn("w:sdt")), None)
        if sdt is not None and _citation_sdt(r_el):
            if id(sdt) not in seen:
                seen.add(id(sdt))
                txt = "".join(x.text or "" for x in sdt.iter(qn("w:t")))
                out.append((_citation_sdt(r_el), txt, pos))
            continue
        for child in r_el:
            if child.tag == qn("w:fldChar"):
                ty = child.get(qn("w:fldCharType"))
                if ty == "begin":
                    stack.append([None, False, "", pos])
                elif ty == "separate" and stack:
                    stack[-1][1] = True
                elif ty == "end" and stack:
                    kind, _, text, at = stack.pop()
                    if stack:                     # nested: its text belongs to the outer field
                        stack[-1][2] += text
                    elif kind in ("endnote", "zotero", "mendeley", "word_citation"):
                        out.append((kind, text, at))
            elif child.tag == qn("w:instrText") and stack and stack[-1][0] is None:
                stack[-1][0] = field_kind(child.text)
            elif child.tag == qn("w:t") and stack and stack[-1][1]:
                stack[-1][2] += child.text or ""
            elif child.tag == qn("w:t") and not stack:
                pos += len(child.text or "")
    return out


EXISTING_RE = re.compile(r"^\s*[\[(]?\s*(" + NUMLIST + r")\s*[\])]?\s*$")


def _citation_sdt(r_el):
    """Kind of the citation content control around a run, or None."""
    for a in r_el.iterancestors():
        if a.tag == qn("w:sdt"):
            pr = a.find(qn("w:sdtPr"))
            if pr is None:
                continue
            if pr.find(qn("w:citation")) is not None or pr.find(qn("w:bibliography")) is not None:
                return "word_citation"
            tag = pr.find(qn("w:tag"))
            val = (tag.get(qn("w:val")) if tag is not None else "") or ""
            k = field_kind(val)
            if k in ("mendeley", "zotero", "endnote"):
                return k
            if "CITATION" in val.upper():
                return "mendeley"
    return None


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
    if len(word) < 3 and word not in GLUED_SHORT_OK:
        return False
    if any(c.isupper() for c in word[1:]) or word.lower() in GLUED_IDENT:
        return False
    if w.start() > 0 and before[w.start() - 1].isdigit():
        return False                      # token such as 1p19q or H3K27me3
    if word.lower() in GLUED_FLAG:
        return f"number attached to '{word}' may be part of the text; left unchanged"
    if word[0].isupper() and len(word) <= 5:
        # a short capitalised name with a number: a cell line or a product ('Molm13', 'Kasumi1')
        return f"number attached to '{word}' may be a name (a cell line?); left unchanged"
    return True


def _lone_year(spec, n_refs):
    """'2019' in brackets or parentheses when the list is shorter: a year, not a citation."""
    s = spec.strip()
    return bool(re.fullmatch(r"(?:18|19|20)\d{2}", s)) and int(s) > n_refs


# a paragraph that opens the manuscript body; superscripts before it are author and affiliation marks
FRONT_END = re.compile(r"^\s*(?:\d+\.?\s*)?(?:abstract|summary|structured abstract|introduction|background|"
                       r"key points|highlights|main text)\b", re.I)


def front_matter_end(paragraph_elements, limit=60):
    """Index of the first paragraph that opens the abstract or the introduction, when it comes in
    the first `limit` paragraphs after some title page text; else 0 (no front matter assumed)."""
    for i, p_el in enumerate(paragraph_elements[:limit]):
        text = "".join(t.text or "" for t in p_el.iter(qn("w:t")))
        if FRONT_END.match(text) and len(text) < 2000:
            return i
    return 0


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


def _ancestors_until(el, stop):
    for a in el.iterancestors():
        if a is stop:
            return
        yield a


def _wrap_any(p_el, paragraph):
    from docx.text.paragraph import Paragraph
    return Paragraph(p_el, paragraph._parent)


def _paragraph_runs(p_el, paragraph):
    """Runs of a paragraph in document order, flagging runs that sit inside a
    field (complex or simple) or a citation content control, so their text is
    never rewritten. Also returns the field instructions / control tags seen."""
    out, stack, field_kinds = [], [], []      # stack: [instruction, past 'separate'] per open field
    seen_sdt = set()
    for r_el in p_el.iter(qn("w:r")):
        if any(a.tag == qn("w:txbxContent") for a in _ancestors_until(r_el, p_el)):
            continue                      # a text box's own paragraphs are processed on their own
        simple = next((a for a in r_el.iterancestors() if a.tag == qn("w:fldSimple")), None)
        if simple is not None:
            if id(simple) not in seen_sdt:
                seen_sdt.add(id(simple))
                field_kinds.append(simple.get(qn("w:instr")) or "")
            out.append((Run(r_el, paragraph), True))
            continue
        sdt_kind = _citation_sdt(r_el)
        if sdt_kind:
            sdt = next(a for a in r_el.iterancestors() if a.tag == qn("w:sdt"))
            if id(sdt) not in seen_sdt:
                seen_sdt.add(id(sdt))
                field_kinds.append({"word_citation": "CITATION", "mendeley": "MENDELEY",
                                    "zotero": "ZOTERO", "endnote": "EN.CITE"}[sdt_kind])
            out.append((Run(r_el, paragraph), True))
            continue
        marks = False
        for child in r_el:
            if child.tag == qn("w:fldChar"):
                marks = True
                ty = child.get(qn("w:fldCharType"))
                if ty == "begin":
                    stack.append(["", False])
                elif ty == "separate" and stack:
                    stack[-1][1] = True
                elif ty == "end" and stack:
                    stack.pop()
            elif child.tag == qn("w:instrText"):
                marks = True
                field_kinds.append((child.text or "").strip())
                if stack:
                    stack[-1][0] += child.text or ""
        # the text shown by a HYPERLINK field ('[5]' linked to the reference list, as PDF converters
        # write it) is plain text and is converted; every other field's text is left alone
        free = all(k.strip().upper().startswith("HYPERLINK") and shown for k, shown in stack)
        out.append((Run(r_el, paragraph), marks or bool(stack) and not free))
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
        self.field_notes = {k: 0 for k in FIELD_KINDS}
        self.location = "body"
        self._pidx = 0          # document order of the paragraph being processed
        self.in_box = False     # processing a paragraph inside a text box
        self.front = False      # on the title page: superscripts are author and affiliation marks
        self.front_marks = 0

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
    def process_paragraph(self, p_el, paragraph, label, in_box=False):
        self._pidx += 1
        self.in_box = in_box
        runs, kinds = _paragraph_runs(p_el, paragraph)
        for k in kinds:
            fk = field_kind(k)
            if fk:
                self.field_notes[fk] += 1
        usable = [(r, locked or any(c.tag in OPAQUE for c in r._r)) for r, locked in runs]
        for kind, shown, at in existing_citations(p_el):
            m = EXISTING_RE.match(shown)
            nums = expand_numbers(m.group(1)) if m else None
            if nums:
                self.log.append(dict(paragraph=label, location=self.location, form=f"existing {kind} field",
                                     order=(self._pidx, at), original=shown, replaced_with="",
                                     refs=",".join(map(str, nums)), problem="", existing=True,
                                     note="reference manager citation, left as it is",
                                     context=shown))
        if self.superscript and self.front:
            self.front_marks += sum(1 for r, lk in usable if not lk and r.text.strip() and _is_superscript(r))
        elif self.superscript:
            self._superscripts(usable, label)
        if self.brackets:
            self._pattern(usable, IEEE_RANGE_RE, label, "brackets", spec=lambda m: f"{m.group(1)}-{m.group(2)}")
            self._pattern(usable, BRACKET_RE, label, "brackets")
        if self.parentheses:
            self._pattern(usable, PAREN_RE, label, "parentheses")
        if self.glued:
            self._pattern(usable, GLUED_RE, label, "glued", accept=glued_ok)
        # paragraphs inside text boxes anchored in this paragraph, each on its own
        boxes = [b for b in p_el.iter(qn("w:txbxContent"))
                 if not any(a.tag == qn("w:txbxContent") for a in b.iterancestors() if a is not p_el)]
        for b in boxes:
            for k, inner in enumerate(b.findall(qn("w:p")), 1):
                self.process_paragraph(inner, _wrap_any(inner, paragraph), f"{label}, text box para {k}", True)
        self.in_box = in_box

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
            m = SUP_RE.fullmatch(text) or SUP_WRAPPED_RE.fullmatch(text)
            before, after = self._context(usable, i, j)
            tail = (m.group(2) or "") if m and m.re is SUP_WRAPPED_RE else ""
            if m:
                nums = expand_numbers(m.group(1))
                problem = ""
                if UNIT_BEFORE.search(before) or SYMBOL_BEFORE.search(before) or (after[:1] and after[:1] in "+-−"):
                    problem = "looks like an exponent or unit; left unchanged"
                elif nums is None:
                    problem = "invalid range (end before start or over 200); left unchanged"
                elif not nums or max(nums) > self.n_refs or min(nums) < 1:
                    problem = "number outside the reference list; left unchanged"
                cit = None if problem else self.citation_for(nums)
                if cit is None and not problem:
                    problem = "reference number not found; left unchanged"
                if cit:
                    group[0].text = cit + tail        # a full stop or comma set in superscript stays
                    group[0].font.superscript = False
                    for g in group[1:]:
                        if g.text:
                            g.text = ""
                offset = sum(len(r.text or "") for r, lk in usable[:i] if not lk)
                self.log.append(dict(paragraph=label, location=self.location, form="superscript",
                                     in_text_box=self.in_box,
                                     order=(self._pidx, offset),
                                     original=text, replaced_with=cit or "",
                                     refs=",".join(map(str, nums or [])), problem=problem,
                                     context=(before + "░" + text + "░" + after).strip()))
            i = j + 1

    def _pattern(self, usable, regex, label, kind, accept=None, spec=None):
        spec = spec or (lambda m: m.group(1))
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
            nums = expand_numbers(spec(m))
            ctx = (text[max(0, m.start() - 60):m.start()] + "░" + m.group(0) + "░" +
                   text[m.end():m.end() + 40]).replace("\x00", "")
            problem = verdict if isinstance(verdict, str) else ""
            if not problem and nums is None and kind != "glued":
                problem = "invalid range (end before start or over 200); left unchanged"
            elif not problem and (not nums or max(nums) > self.n_refs or min(nums) < 1):
                if kind == "glued" or _lone_year(spec(m), self.n_refs):
                    continue              # 'week2024', '(2019)': not a citation at all
                problem = "number outside the reference list; left unchanged"
            cit = None if problem else self.citation_for(nums)
            if cit is None and not problem:
                problem = "reference number not found; left unchanged"
            if cit:
                self._replace_span(segs, m.start(), m.end(), cit)
            self.log.append(dict(paragraph=label, location=self.location, form=kind, in_text_box=self.in_box,
                                 order=(self._pidx, m.start()),
                                 original=m.group(0), replaced_with=cit or "",
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
                  remove_reference_list=True, stats=None, body_start_para=None, **opts):
    """Write an annotated copy of `src` to `dst` (never `src` itself).
    Returns (citation_log, warnings); `stats`, if given, receives field counts."""
    if str(src) == str(dst):
        raise ValueError("refusing to overwrite the original document")
    doc = Document(str(src))
    body = doc.element.body
    top = list(doc.paragraphs)
    skip = set()
    if heading_para is not None:
        end = reflist_end_para if reflist_end_para is not None else heading_para
        skip = {top[i]._p for i in range(heading_para, min(end, len(top) - 1) + 1)}
        # a reference list kept in a table: every reference is tied to the heading paragraph and
        # the table(s) sit between the heading and the next paragraph
        stop = top[end + 1]._p if end + 1 < len(top) else None
        inside = False
        for el in (body.iterchildren() if end == heading_para else ()):
            if el is top[heading_para]._p:
                inside = True
            elif el is stop:
                break
            elif inside and el.tag == qn("w:tbl"):
                skip.add(el)

    ann = Annotator(ref_to_rec, rec_keys, n_refs=max(ref_to_rec) if ref_to_rec else 0, **opts)
    front = front_matter_end([p._p for p in top[: heading_para if heading_para is not None else len(top)]])
    para_no = 0
    for el in body.iterchildren():
        if el.tag == qn("w:p"):
            para_no += 1
            ann.front = para_no <= front
            if el in skip or (body_start_para and para_no - 1 < body_start_para):
                continue                  # another chapter's text, cited against its own list
            ann.process_paragraph(el, _wrap(el, doc), f"P{para_no}")
        elif el.tag == qn("w:tbl"):
            if el in skip or (body_start_para and para_no - 1 < body_start_para):
                continue
            for k, p_el in enumerate(el.iter(qn("w:p")), 1):
                ann.process_paragraph(p_el, _wrap(p_el, doc), f"table near P{para_no}, cell para {k}")

    ann.front = False
    for kind, p_el, label in _note_paragraphs(doc):
        ann.location = kind
        ann.process_paragraph(p_el, _wrap(p_el, doc), label)
    ann.location = "body"
    _flush_note_parts(doc)

    removed_tables, removed_fields = 0, {}
    if remove_reference_list and heading_para is not None and reflist_end_para is not None:
        for i in range(heading_para + 1, min(reflist_end_para, len(top) - 1) + 1):
            p = top[i]._p
            for it in p.iter(qn("w:instrText")):
                k = field_kind(it.text)
                if k:
                    removed_fields[k] = removed_fields.get(k, 0) + 1
            p.getparent().remove(p)
        for el in skip:
            if el.tag == qn("w:tbl") and el.getparent() is not None:
                el.getparent().remove(el)
                removed_tables += 1

    doc.save(str(dst))

    warnings = []
    fn = ann.field_notes
    managed = {k: fn[k] for k in ("endnote", "zotero", "mendeley", "word_citation") if fn[k]}
    if managed:
        names = {"endnote": "EndNote", "zotero": "Zotero", "mendeley": "Mendeley", "word_citation": "Word"}
        warnings.append(
            "The document already contains reference manager citations ("
            + ", ".join(f"{v} {names[k]}" for k, v in managed.items())
            + "). Those were left exactly as they are; only plain text citations were converted.")
    if fn["crossref"]:
        warnings.append(
            f"{fn['crossref']} citations are Word cross reference fields and were not converted. "
            "To include them, select those citations in Word and press Ctrl+Shift+F9 to turn them into "
            "plain text (this unlinks every field in the selection), then run again.")
    boxed = [e for e in ann.log if e["replaced_with"] and e.get("in_text_box")]
    if boxed:
        warnings.append(f"{len(boxed)} citations in text boxes were converted; some EndNote versions do not "
                        "format citations in text boxes, so check them after Update Citations and Bibliography.")
    if ann.front_marks:
        warnings.append(f"{ann.front_marks} superscripts before the abstract or introduction were taken as "
                        "author and affiliation marks and left unchanged.")
    bad = [e for e in ann.log if e["problem"]]
    if bad:
        warnings.append(f"{len(bad)} possible citations were left unchanged; see the In text citations sheet.")
    if stats is not None:
        stats["field_notes"] = dict(fn)
        stats["removed_tables"] = removed_tables
        stats["removed_fields"] = removed_fields
    return ann.log, warnings


def _note_parts(doc):
    """(kind, part) for the footnotes and endnotes parts, if any."""
    for rel in doc.part.rels.values():
        rt = rel.reltype or ""
        if rel.is_external:
            continue
        if rt.endswith("/footnotes"):
            yield "footnote", rel.target_part
        elif rt.endswith("/endnotes"):
            yield "endnote", rel.target_part


def _part_element(part):
    """XML root of a part; generic parts are parsed once and written back later."""
    if hasattr(part, "element"):
        return part.element
    if not hasattr(part, "_refcheck_el"):
        from docx.oxml.parser import parse_xml
        part._refcheck_el = parse_xml(part.blob)
    return part._refcheck_el


def _flush_note_parts(doc):
    from lxml import etree
    for _, part in _note_parts(doc):
        if hasattr(part, "_refcheck_el"):
            part._blob = etree.tostring(part._refcheck_el, xml_declaration=True,
                                        encoding="UTF-8", standalone=True)


def _note_paragraphs(doc):
    for kind, part in _note_parts(doc):
        root = _part_element(part)
        tag = qn("w:footnote") if kind == "footnote" else qn("w:endnote")
        for note in root.iter(tag):
            if note.get(qn("w:type")) in ("separator", "continuationSeparator", "continuationNotice"):
                continue
            nid = note.get(qn("w:id"))
            for k, p_el in enumerate(note.iter(qn("w:p")), 1):
                yield kind, p_el, f"{kind} {nid}" + (f", para {k}" if k > 1 else "")


def document_structure(path):
    """Counts used to show that conversion preserved the document's structure."""
    doc = Document(str(path))
    body = doc.element.body
    notes = {"footnote": 0, "endnote": 0}
    for kind, part in _note_parts(doc):
        root = _part_element(part)
        tag = qn("w:footnote") if kind == "footnote" else qn("w:endnote")
        notes[kind] = sum(1 for n in root.iter(tag)
                          if n.get(qn("w:type")) not in ("separator", "continuationSeparator", "continuationNotice"))
    fields = {k: 0 for k in FIELD_KINDS}
    for it in body.iter(qn("w:instrText")):
        k = field_kind(it.text)
        if k:
            fields[k] += 1
    return dict(paragraphs=len(doc.paragraphs), tables=len(doc.tables),
                images=sum(1 for _ in body.iter(qn("w:drawing"))) + sum(1 for _ in body.iter(qn("w:pict"))),
                sections=len(doc.sections), footnotes=notes["footnote"], endnotes=notes["endnote"],
                equations=sum(1 for _ in body.iter("{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath")),
                fields=fields)


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
    front = {p._p for p in top[:front_matter_end([p._p for p in top[: heading_para or len(top)]])]}
    for el in doc.element.body.iterchildren():
        if el is stop:
            break
        if el in front:
            continue
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
