"""Read a manuscript or reference list and split it into individual references.

Supported inputs
    .docx            Word (typed numbers or Word auto numbering)
    .doc / .odt/.rtf converted with LibreOffice when it is installed
    .pdf             text layer (pypdf)
    .txt / .md       plain text
    .rtf             plain text after stripping RTF control words
    .csv / .tsv      one reference per row (auto detects the citation column)
    .xlsx            same as CSV, first sheet

Each reference is returned as a dict:
    num, text, authors, title, journal, year, volume, issue, pages, doi,
    pmid_hint, parse_note, para_start, para_end
"""

import csv
import io
import os
import re
import shutil
import subprocess  # nosec B404 - only LibreOffice, argument list, no shell
import tempfile
from pathlib import Path

from .textutil import DASH_RE, clean_text, extract_year, first_author_surname, norm_dashes

TEXT_EXT = {".txt", ".md", ".text", ".markdown"}
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xlsm"}
LATEX_EXT = {".bib", ".bbl", ".tex"}


# --------------------------------------------------------------- regexes ----

DEFAULT_HEADINGS = (
    r"references?(\s+list)?|bibliography|literature\s+cited|works\s+cited|"
    r"cited\s+literature|reference\s+list|references\s+and\s+notes|notes\s+and\s+references|"
    r"references\s+cited"
)

STOP_HEADING = re.compile(
    r"^\s*(tables?\s+(&|and)\s+figures?|figure\s+legends?|figures?\b|tables?\b|"
    r"appendix|appendices|supplementar(?:y|ies|ily)\b|acknowledg(e)?ments?|"
    r"funding\b|conflicts?\s+of\s+interest|competing\s+interests?|declarations?\b|"
    r"author\s+contributions?|data\s+availability|abbreviations\b|"
    r"publisher[\u2019']?s\s+note|authors?\s+and\s+affiliations|author\s+information|"
    r"additional\s+information|ethics\s+declarations|rights\s+and\s+permissions|"
    r"about\s+this\s+article|chapter\s+\d+\s*$|chapter\s+\d+\s*[:.]\s+[A-Z])",
    re.I,
)

# "1. Smith"  "1) Smith"  "[1] Smith"  "[1]Smith"; not "10.3389/..." (a wrapped DOI)
# '1. Smith', '[1] Smith', '1) Smith', and '1.Smith' or '1.van de Donk' (no space, as pasted from a PDF).
# Not '2.5 mg' (a digit follows) and not a bare '1van' (a wrapped line may start '2nd ed.' or '3D').
NUM_START = re.compile(r"^\s*(?:\[(\d{1,4})\]\s*|(\d{1,4})\s*[.)](?:\s+|(?=[A-Za-z\u00C0-\u024F])))(?=\S)")


NUM_BARE = re.compile(r"^\s*(\d{1,4})\s+(?=[A-Z\u00C0-\u024F]|(?:van|von|de|der|den|del|da|di|du|le|la|dos|das|ten|ter)\s)")


def _num(m):
    return int(m.group(1) or m.group(2))
IGNORE_LINE = re.compile(
    r"^\s*(official|confidential|page\s+\d+(\s+of\s+\d+)?|\d{1,4})\s*$", re.I
)

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# "Journal. 2021;23(8):1231-1251."  "Blood. 2021 Jan 7;137(1):1."
# "medRxiv. 2026. doi:..."  "Lancet. 2020. [Epub ahead of print]"
# a locator printed straight after the year when there is no volume: a PII, a preprint ID, an
# arXiv ID or an article number. One token that holds a digit; 'vol(issue):pages' is excluded.
_LOCATOR = r"(?!\d+(?:\([^)]*\))?:)(?=[^\s;]*\d)[^\s;]+?"
YEAR_ANCHOR = re.compile(
    r"(?<=[.?!] )(?:19|20)\d{2}[a-z]?"
    r"(?:\s+" + _MONTH + r"(?:\s*" + DASH_RE + r"\s*" + _MONTH + r")?(?:\s+\d{1,2})?)?"
    r"(?=\s*;|\s*:\s*\d|\s*\.\s*(?:doi\b|https?:|epub\b|pmid\b|\[|published\b)"
    r"|\s*\.?\s*$|\s*\.\s*\d+\s*\(|\s*,\s*\d"
    # 'J Vis Exp. 2026(234).'  'Blood. 2024(Suppl 1):abstract 12': the year then an issue, no volume
    r"|\s*\([^)]*\d[^)]*\)\s*(?:[:,;]|\.?\s*$|\.\s*(?:doi\b|https?:|epub\b|pmid\b))"
    # 'J Am Acad Dermatol. 2024:S0190-9622(24)02755-5.'  'Res Sq. 2024:rs.3.rs-5390104.'
    # 'ArXiv. 2024:arXiv:2412.14338v1.': the year then a locator, no volume
    r"|\s*:\s*" + _LOCATOR + r"\.?\s*(?:$|\.\s*(?:doi\b|https?:|epub\b|pmid\b)))",
    re.I,
)
APA_RE = re.compile(
    r"^(?P<authors>.+?)\s*\((?P<year>(?:19|20)\d{2})[a-z]?(?:,[^)]*)?\)\.\s*(?P<rest>.+)$"
)
# Old Wiley DOIs are SICI strings with < > ; : inside (10.1002/1097-0142(197509)36:3<842::AID-...>3.0.CO;2-U)
DOI_RE = re.compile(r"(10\.\d{4,9}/(?:[^\s\"<>]|<[^\s\"<>]*>)+)", re.I)
PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d{5,9})\b", re.I)


# --------------------------------------------------------------- readers ----

def _convert_with_soffice(path, workdir=None):
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise RuntimeError(
            f"Cannot read {Path(path).suffix} files without LibreOffice. "
            "Save the file as .docx (Word: File > Save As) and try again."
        )
    if workdir is None:
        out = tempfile.mkdtemp(prefix="refcheck_")
        from .privacy import register_for_cleanup
        register_for_cleanup(out)          # removed when the program exits
    else:
        out = str(workdir)
    subprocess.run([exe, "--headless", "--convert-to", "docx", "--outdir", out, str(path)],  # nosec B603 - fixed argument list, no shell
                   check=True, capture_output=True, timeout=180)
    conv = Path(out) / (Path(path).stem + ".docx")
    if not conv.exists():
        raise RuntimeError("LibreOffice conversion failed.")
    return conv


MAX_DOCX_UNPACKED_MB = 200       # all parts of the Word file, uncompressed
MAX_DOCX_PARAGRAPHS = 100_000     # a long thesis has about 20,000


def check_docx_limits(path, max_mb=MAX_DOCX_UNPACKED_MB, max_paragraphs=MAX_DOCX_PARAGRAPHS):
    """Refuse a Word file that would take minutes or gigabytes to read (a zip bomb or a
    paragraph flood) before python-docx parses it. Raises ValueError with a plain reason."""
    import zipfile
    try:
        with zipfile.ZipFile(str(path)) as z:
            total = sum(i.file_size for i in z.infolist())
            if total > max_mb * 1024 * 1024:
                raise ValueError(f"This Word file unpacks to {total // (1024 * 1024)} MB, more than the "
                                 f"{max_mb} MB RefCheck reads. Save a copy without embedded media and try again.")
            n = 0
            with z.open("word/document.xml") as fh:
                tail = b""
                for block in iter(lambda: fh.read(1 << 20), b""):
                    chunk = tail + block
                    n += chunk.count(b"<w:p>") + chunk.count(b"<w:p ") + chunk.count(b"<w:p/>")
                    tail = chunk[-5:]
                    n -= tail.count(b"<w:p>") + tail.count(b"<w:p ") + tail.count(b"<w:p/>")
                n += tail.count(b"<w:p>") + tail.count(b"<w:p ") + tail.count(b"<w:p/>")
            if n > max_paragraphs:
                raise ValueError(f"This Word file has {n} paragraphs, more than the {max_paragraphs} RefCheck "
                                 "reads. Copy the reference list into a new document and check that instead.")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ValueError(f"This is not a readable Word (.docx) file ({type(e).__name__}).") from e


def read_docx_lines(path, heading=None):
    """[(text, starts_numbered_item, para_index)] using python-docx.

    Paragraph indices refer to Document.paragraphs (top level paragraphs), which
    the annotator uses to remove the reference list."""
    from docx import Document

    check_docx_limits(path)
    doc = Document(str(path))

    def style_numbered(style):
        while style is not None:
            ppr = style.element.pPr
            if ppr is not None and ppr.numPr is not None:
                return True
            style = style.base_style
        return False

    from docx.oxml.ns import qn
    from docx.table import Table
    hre = _heading_re(heading)
    paras = doc.paragraphs
    pos = {id(p._p): i for i, p in enumerate(paras)}
    out, last_text = [], None             # last_text: index of the last non-empty paragraph
    for el in doc.element.body.iterchildren():
        if el.tag == qn("w:tbl"):
            # a reference list kept in a table (number | reference) right under the heading: one
            # line per row, tied to the heading paragraph so the list stays inside the document
            if last_text is not None and hre.match(re.sub(r"\s+", " ", paras[last_text].text).strip()):
                for row in Table(el, doc._body).rows:
                    cells = []
                    for c in row.cells:
                        if not cells or c._tc is not cells[-1][0]:
                            cells.append((c._tc, " ".join(c.text.split())))
                    text = " ".join(s for _, s in cells if s)
                    if text:
                        out.append((text, False, last_text))
            continue
        if el.tag != qn("w:p"):
            continue
        i = pos[id(el)]
        p = paras[i]
        text = p.text
        if not text.strip():
            out.append(("", False, i))       # empty: skip the slow style lookup
            continue
        last_text = i
        pPr = p._p.pPr
        numbered = (pPr is not None and pPr.numPr is not None) or style_numbered(p.style)
        lines = text.split("\n")
        for k, ln in enumerate(lines):
            out.append((ln, numbered and k == 0, i))
    return out


def read_pdf_lines(path):
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("Reading PDF needs:  pip install pypdf") from e
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            ok = reader.decrypt("")          # many PDFs are locked against editing only
        except Exception:
            ok = 0
        if not ok:
            raise ValueError("This PDF is password protected. Save an unprotected copy (or use the Word "
                             "file) and run again.")
    pages, placed = [], []
    for pg in reader.pages:
        frags = []
        text = pg.extract_text(visitor_text=lambda s, cm, tm, fd, fs: frags.append((tm[5] * cm[3] + cm[5], s)))
        notes = footnote_lines(frags, float(pg.mediabox.height))
        pages.append([ln for ln in (text or "").splitlines() if " ".join(ln.split()) not in notes])
        rows = {}
        for y, s in frags:
            rows[round(y / 4)] = rows.get(round(y / 4), "") + s
        placed.append({(" ".join(s.split()), y) for y, s in rows.items() if s.strip()})
    if pages and sum(len("".join(p).strip()) for p in pages) < 40 * len(pages):
        raise ValueError("This PDF has almost no text to read: it is probably a scanned image. Run text "
                         "recognition (OCR) on it first, or use the Word file.")
    furniture = page_furniture(placed)
    lines = [_greek_back(ln) for page in drop_page_margins([drop_number_column(p) for p in pages]) for ln in page
             if " ".join(ln.split()) not in furniture]
    lines = strip_line_numbers(lines)
    return [(ln, False, None) for ln in lines]


def page_furniture(placed):
    """Watermarks and running heads ('In review', 'CONFIDENTIAL'): short lines printed at the same
    height on at least three pages and on half of them. `placed` holds one set of (text, height)
    per page. A line of a reference that wraps the same way in several references ('discontinuation
    in chronic myeloid') sits at different heights and is kept; counting repeats alone dropped it."""
    from collections import Counter
    n = len(placed)
    if n < 3:
        return set()
    seen = Counter(k for page in placed for k in page if 0 < len(k[0]) <= 60 and not NUM_START.match(k[0]))
    need = max(3, (n + 1) // 2)
    return {text for (text, _), c in seen.items() if c >= need and not re.fullmatch(r"\d{1,4}", text)}


def strip_line_numbers(lines):
    """Remove manuscript line numbers glued to the end or start of each line
    ('References516', '517 1. Smith J'). The numbering is followed as an exact
    sequence (n, n+1, ...), so only the line number itself is removed and
    documents without line numbers are left alone."""
    st = [l.strip() for l in lines]

    def has(s, v, at_end, strict=True):
        t = str(v)
        if at_end:
            # while looking for the sequence, a number after a dash may be a page range ('610-4');
            # once the sequence is known, the expected number is the line number even after a word
            # broken at the line end ('State-536', '163\u2013614' for '163\u201370' on the next line)
            return s.endswith(t) and len(s) > len(t) and not (strict and s[: -len(t)].endswith(("-", "\u2013")))
        return s.startswith(t + " ")

    def find_start(at_end):
        for i, s in enumerate(st):
            m = re.search(r"\d{1,6}$", s) if at_end else re.match(r"\d{1,6}(?= )", s)
            if not m:
                continue
            d = m.group()
            for L in range(min(4, len(d)), 0, -1):   # longest, most specific first
                v = int(d[-L:]) if at_end else int(d[:L]) if L == len(d) else None
                if v is None:
                    continue
                ok, exp = 0, v + 1
                for s2 in st[i + 1:i + 25]:
                    if has(s2, exp, at_end):
                        ok, exp = ok + 1, exp + 1
                if ok >= 8:
                    return i, v
        return None

    for at_end in (True, False):
        start = find_start(at_end)
        if not start:
            continue
        i0, exp = start
        out = st[:i0]
        for s2 in st[i0:]:
            for skip in range(0, 4):            # tolerate a few skipped numbers
                if has(s2, exp + skip, at_end, strict=skip > 0):
                    t = str(exp + skip)
                    s2 = s2[: -len(t)].rstrip() if at_end else s2[len(t):].lstrip()
                    exp += skip + 1
                    break
            else:
                # resynchronise after a jump (figures, tables, page breaks)
                m = re.search(r"(?<![\d.])(\d{1,4})$", s2) if at_end else re.match(r"(\d{1,4}) ", s2)
                if m and exp < int(m.group(1)) <= exp + 150 and len(s2) > len(m.group(1)):
                    s2 = (s2[: m.start()] if at_end else s2[m.end():]).strip()
                    exp = int(m.group(1)) + 1
            out.append(s2)
        return out
    return st


_NOTE_MARK = re.compile(r"^\s*(?:\d{1,3}\s?(?=[A-Z])|[*\u2020\u2021\u00a7]|[a-z]\s(?=[A-Z]))")
_PAGE_FURNITURE = re.compile(r"^\s*(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.I)


def footnote_lines(frags, height):
    """Footnotes at the foot of a PDF page, as whitespace normalised lines: the block in the bottom
    quarter of the page below a gap of at least twice the usual line spacing, opening with a note
    mark ('1 See the original method [21].'). `frags` are (y, text) pieces from pypdf, y upwards.
    Without this a footnote is read as the end of the reference printed above it."""
    rows = {}
    for y, s in frags:
        if s.strip():
            key = round(y)
            rows[key] = rows.get(key, "") + s
    ys = sorted(rows, reverse=True)
    body = [y for y in ys if not _PAGE_FURNITURE.match(rows[y])]
    if len(body) < 4:
        return set()
    gaps = sorted(a - b for a, b in zip(body, body[1:]) if a - b > 1)
    if not gaps:
        return set()
    usual = gaps[len(gaps) // 2]
    for a, b in zip(body, body[1:]):
        if a - b >= 2 * usual and b < height * 0.25 and _NOTE_MARK.match(rows[b]) and not NUM_START.match(rows[b]):
            below = [y for y in body if y <= b]
            if len(below) <= 8:
                return {" ".join(rows[y].split()) for y in below}
    return set()


_GREEK_TAIL = re.compile(r"\s([\u0370-\u03ff])\s*$")
_GREEK_HOLE = re.compile(r"(?<=\w-) (?=[\w(])|(?<=\w)  (?=\w)")


def _greek_back(line):
    """A Greek letter set in a fallback font is written by the PDF text layer at the end of its
    line, leaving a hole where it stood: 'interferon- 2b and ... diagnosed \u03b1'. Put it back
    into the hole when there is exactly one ('interferon-\u03b12b')."""
    m = _GREEK_TAIL.search(line)
    if not m:
        return line
    body = line[: m.start()]
    holes = list(_GREEK_HOLE.finditer(body))
    if len(holes) != 1:
        return line
    h = holes[0]
    return body[: h.start()] + m.group(1) + body[h.end():] + line[m.end():]


def drop_number_column(page, min_run=5):
    """Drop line numbers that the PDF text layer gives as a block of their own ('47', '48', ...
    after the page text, as LibreOffice and some Word versions write them): runs of at least
    `min_run` lines that are consecutive integers and nothing else."""
    out, i = [], 0
    while i < len(page):
        j = i
        while j < len(page) and re.fullmatch(r"\d{1,5}", page[j].strip()) and \
                (j == i or int(page[j].strip()) == int(page[j - 1].strip()) + 1):
            j += 1
        if j - i >= min_run:
            i = j
            continue
        out.append(page[i])
        i += 1
    return out


def drop_page_margins(pages):
    """Drop running heads and footers: a line among the first or last two of a page that recurs
    there on at least two pages and on half of them ('Page 2', 'Journal | Article'), numbers
    ignored. Short documents of two or three pages are covered too (drop_repeated_lines needs
    three copies)."""
    from collections import Counter
    if len(pages) < 2:
        return pages
    key = lambda s: re.sub(r"\d+", "#", " ".join(s.split()))
    def edges(page):
        idx = [i for i, l in enumerate(page) if l.strip()]
        return set(idx[:2] + idx[-2:])
    seen = Counter()
    for page in pages:
        seen.update({key(page[i]) for i in edges(page)})
    need = max(2, (len(pages) + 1) // 2)
    # a running head has words ('Page #', 'Journal | Article'); a bare page number is '#'. A line of
    # numbers and punctuation such as '2013;369(1):32-42.' is the end of a reference, whatever its digits
    rep = {k for k, c in seen.items() if c >= need and 0 < len(k) <= 80 and not NUM_START.match(k)
           and (k == "#" or len(re.findall(r"[A-Za-z]", k)) >= 3)}
    return [[l for i, l in enumerate(page) if not (i in edges(page) and key(l) in rep)] for page in pages]


def drop_repeated_lines(lines, min_count=3):
    """Drop running headers, footers and watermarks ('In review')."""
    from collections import Counter
    cnt = Counter(l.strip() for l in lines if 0 < len(l.strip()) <= 60)
    rep = {t for t, c in cnt.items() if c >= min_count and not NUM_START.match(t)
           and not re.fullmatch(r"\d{1,4}", t)}
    return [l for l in lines if l.strip() not in rep]


def _rtf_to_text(raw):
    try:
        from striprtf.striprtf import rtf_to_text
        return rtf_to_text(raw)
    except ImportError:
        pass
    txt = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", "replace"), raw)
    txt = re.sub(r"\{\\\*[^{}]*\}", "", txt)
    txt = re.sub(r"\\par[d]?\b ?", "\n", txt)
    txt = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", txt)
    return txt.replace("{", "").replace("}", "")


def read_text_lines(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if Path(path).suffix.lower() == ".rtf":
        text = _rtf_to_text(text)
    return [(ln, False, None) for ln in text.splitlines()]


def read_table(path):
    ext = Path(path).suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        ws = load_workbook(str(path), read_only=True, data_only=True).worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        head = [str(h or f"col{i+1}") for i, h in enumerate(rows[0])]
        return [{head[i]: ("" if v is None else str(v)) for i, v in enumerate(r) if i < len(head)}
                for r in rows[1:] if any(v not in (None, "") for v in r)]
    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    delim = "\t" if ext == ".tsv" else None
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(text[:4096], delimiters=",\t;").delimiter
        except csv.Error:
            delim = ","
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


# ------------------------------------------------------------ parsing -------

def _blank(text=""):
    return dict(num=None, text=text, authors="", title="", journal="", year="",
                volume="", issue="", pages="", doi="", pmid_hint="",
                parse_note="", para_start=None, para_end=None, parse_method="", confidence={})


def _parse_tail(tail, rec):
    """';27(8):2035-2046.'  ';6:e123'  ' 43(6):676-81.'  ';27(Supplement_4):iv1-iv66'"""
    lm = re.match(r"\s*:\s*(?P<loc>" + _LOCATOR + r")\.?\s*(?:$|\.\s*(?:doi\b|https?:|epub\b|pmid\b))", tail, re.I)
    if lm:                                   # 'Year:locator', no volume: the locator is the page field
        rec["volume"], rec["issue"], rec["pages"] = "", "", lm.group("loc")
        return
    tail = re.sub(r"^\s*[;:,]?\s*(?:Volume|Vol\.?)\s*", " ", tail, flags=re.I)
    m = re.match(
        r"\s*[;:,]?\s*(?P<vol>[^\s(:;.,]+)?\s*(?:\((?P<iss>[^)]*)\))?"
        r"\s*(?:[:,]\s*(?:pp?\.\s*)?(?P<pg>[A-Za-z]*\d+[A-Za-z]*(?:\s*(?:" + DASH_RE +
        r"|\bto\b)\s*[A-Za-z]*\d+[A-Za-z]*)?))?",
        tail,
    )
    if not m:
        return
    vol = (m.group("vol") or "").strip()
    if re.fullmatch(r"(?:doi|epub|https?|pmid|available|accessed)\w*", vol, re.I):
        return
    rec["volume"] = vol
    rec["issue"] = (m.group("iss") or "").strip()
    pg = norm_dashes(m.group("pg") or "")
    rec["pages"] = re.sub(r"\s*(?:-|\bto\b)\s*", "-", pg).strip()


_AUTHORISH = re.compile(
    r"(?:\bet\s+al\b|^(?:[A-Z][\w'’\-]+(?:\s+[a-z]{1,3})?(?:\s+[\w'’\-]+)?\s+[A-Z]{1,4}"
    r"(?:\s*,\s*|$))+)")


def _split_prefix(prefix, rec):
    """'Authors. Title. Journal' (journal may be empty) -> fields."""
    prefix = prefix.strip().rstrip(".")
    bounds = [m.start() for m in re.finditer(r"[.?!](?=\s|$)", prefix)]
    # a period inside author initials ("Smith J.R.") is not a boundary
    bounds = [b for b in bounds
              if not (re.search(r"(?:^|[\s,.])[A-Z]$", prefix[:b])
                      and re.match(r"\s?[A-Z]\.|,", prefix[b + 1:b + 4]))]
    keep = lambda b: b + (1 if prefix[b] in "?!" else 0)
    if len(bounds) >= 2:
        rec["authors"] = prefix[: bounds[0]].strip()
        rec["title"] = prefix[bounds[0] + 1: keep(bounds[-1])].strip()
        rec["journal"] = prefix[bounds[-1] + 1:].strip()
    elif len(bounds) == 1:
        head, tail = prefix[: bounds[0]].strip(), prefix[bounds[0] + 1:].strip()
        if _AUTHORISH.search(head) and len(tail) > 25:
            rec["authors"], rec["title"] = head, tail        # preprint / no journal
        elif _AUTHOR_LIST_ONLY.match(head):
            if len(tail.split()) > 7 or ":" in tail:
                rec["authors"], rec["title"] = head, tail    # a title with no journal after it
            else:
                rec["authors"], rec["journal"] = head, tail  # no title: 'Mahon FX, et al. Lancet Oncol.'
            rec["parse_note"] = ""
        else:
            rec["title"] = prefix[: keep(bounds[0])].strip()  # no authors listed
            rec["journal"] = tail
    else:
        rec["title"] = prefix
        rec["parse_note"] = "could not separate authors / title / journal"


# "Sci Rep. 15. England(2025). p. 2204."  (EndNote output for online first items)
COUNTRY_YEAR = re.compile(
    r"\.\s+(?:(?P<vol>\w+)\.\s+)?(?P<country>[A-Z][A-Za-z .]*[a-z])\((?P<year>(?:19|20)\d{2})\)\.?"
    r"(?:\s*p\.\s*(?P<pg>[\w\-– ]+?))?\.?\s*$")
# "Semin Oncol (2016) 43(6):676-81."  (Frontiers)
PAREN_YEAR = re.compile(r"(?<=\S)\s\((?P<year>(?:19|20)\d{2})[a-z]?\)(?!\.)")
# "J Clin Oncol 2025;10(8):2616"  "Blood 2021."  (no period before the year)
LOOSE_YEAR = re.compile(
    r"(?<=[A-Za-z)] )(?:19|20)\d{2}[a-z]?(?=\s*;|\s*:\s*\d|\s*\.?\s*$|\s*\.\s*(?:doi|https?:|epub|pmid))",
    re.I)


# a whole segment that is only an author list: 'Mahon FX, et al', 'Mahon FX, Rea D', 'Smith J'
_AUTHOR_LIST_ONLY = re.compile(
    r"^(?:(?:van|von|de|der|den|del|da|di|du|le|la)\s)*[A-Z\u00C0-\u00DE][\w'\u2019\-]+(?:\s[A-Z\u00C0-\u00DE][\w'\u2019\-]+)?"
    r"\s[A-Z]{1,4}(?:,\s*(?:(?:van|von|de|der|den|del|da|di|du|le|la)\s)*[A-Z\u00C0-\u00DE][\w'\u2019\-]+\s[A-Z]{1,4})*"
    r"(?:,?\s*et\s+al)?\.?$")


def _parse_core(core, rec):
    m = COUNTRY_YEAR.search(core)
    if m:
        rec["year"] = m.group("year")
        rec["volume"] = m.group("vol") or ""
        rec["pages"] = re.sub(r"\s+", "", norm_dashes(m.group("pg") or "")).strip(".")
        _split_prefix(core[: m.start()], rec)
        rec["parse_method"] = "endnote_country_year"
        return True
    ys = list(YEAR_ANCHOR.finditer(core))
    if ys:
        ym = ys[-1]
        rec["year"] = ym.group(0)[:4]
        _split_prefix(core[: ym.start()], rec)
        _parse_tail(core[ym.end():], rec)
        rec["parse_method"] = "vancouver"
        return True
    ps = list(PAREN_YEAR.finditer(core))
    if ps:
        pm = ps[-1]
        rec["year"] = pm.group("year")
        _split_prefix(core[: pm.start()], rec)
        _parse_tail(core[pm.end():], rec)
        rec["parse_method"] = "paren_year"
        return True
    if APA_RE.match(core):
        rec["parse_method"] = "apa"
        return _parse_apa(core, rec)
    ls = list(LOOSE_YEAR.finditer(core))
    if ls:
        lm = ls[-1]
        rec["year"] = lm.group(0)[:4]
        _split_prefix(core[: lm.start()], rec)
        _parse_tail(core[lm.end():], rec)
        rec["parse_method"] = "loose_year"
        return True
    return False


def _parse_apa(ref, rec):
    m = APA_RE.match(ref)
    if not m:
        return False
    rec["authors"], rec["year"] = m.group("authors").strip(" ,"), m.group("year")
    rest = m.group("rest")
    # the title ends at the first sentence break, except after an abbreviation ('vs.', 'e.g.'),
    # and except a '?' or '!' followed by a subtitle when a later full stop leaves 'Journal, 15(2)'
    breaks = [c for c in re.finditer(r"[.?!](?=\s)", rest)
              if not re.search(r"\b(?:vs|e\.g|i\.e|cf|approx|al)$", rest[: c.start()], re.I)]
    b = breaks[0] if breaks else None
    if b and rest[b.start()] in "?!":
        for c in breaks[1:]:
            if rest[c.start()] == "." and re.match(
                    r"\s*[A-Z][^.?!,]*(?:,\s*[A-Z&][^.?!,\d]*)*,\s*[A-Za-z]?\d", rest[c.end():]):
                b = c
                break
    if not b:
        rec["title"] = rest.strip(" .")
        return True
    rec["title"] = rest[: b.start() + (1 if rest[b.start()] in "?!" else 0)].strip()
    after = rest[b.end():].strip()
    # the journal ends at ', volume(issue), pages': a journal name may hold commas itself
    # ('Journal of immunology (Baltimore, Md. : 1950), 182(1), 240-9')
    vm = list(re.finditer(r",\s*(?=\d+[A-Za-z]?\s*(?:\([^)]*\))?\s*,\s*[A-Za-z]{0,2}\d)", after))
    jm = re.match(r"(?P<j>[^,]+),\s*(?P<tail>.*)$", after)
    if vm and "," in after[:vm[-1].start()]:
        rec["journal"] = after[:vm[-1].start()].strip(" .")
        _parse_tail(after[vm[-1].start():], rec)
    elif jm:
        rec["journal"] = jm.group("j").strip(" .")
        _parse_tail(", " + jm.group("tail"), rec)
    return True


NOTE_BRACKETS = re.compile(
    r"\s*\[(?:internet|serial on the internet|verify|cited[^\]]*|epub[^\]]*|complete[^\]]*|"
    r"accessed[^\]]*|updated[^\]]*|online[^\]]*|preprint|abstract)\]", re.I)


def _join_spaced_urls(ref):
    """'https://doi.org/10.1038/s430 18-025-00976-5' -> close the gaps inside URLs
    (letter spaced PDFs). The URL runs until the next capitalised word."""
    out, pos = [], 0
    for m in re.finditer(r"h ?t ?t ?p ?s? ?: ?/ ?/", ref):
        if m.start() < pos:
            continue
        rest = ref[m.start():]
        end = re.search(r"\s(?=[A-Z][a-z]{2,}\b)|$", rest).start()
        url = rest[:end]
        out.append(ref[pos:m.start()])
        out.append(url.replace(" ", "") if url.count(" ") >= 1 else url)
        pos = m.start() + end
    out.append(ref[pos:])
    return "".join(out)


_WEB_SOURCE = re.compile(r"\bAvailable (?:from|at)\b|\bAccessed\b|\[Internet\]|\bcited \d{4}|"
                         r"https?://(?!(?:dx\.)?doi\.org)", re.I)


def parse_reference_text(text):
    """Split one formatted reference into fields.
    Handles Vancouver/AMA, Frontiers 'Journal (Year)', APA, styles without a period
    before the year, and EndNote's 'Journal. 15. Country(Year).' output."""
    ref = re.sub(r"\s+", " ", clean_invisible(str(text))).strip()
    ref = re.sub(r"\s+([.,;:])", r"\1", ref)
    ref = NUM_START.sub("", ref, count=1) if NUM_START.match(ref) else ref
    as_written = ref                         # the reference as the user wrote it, for reports
    # AMA, ahead of print: 'Journal. Published online [Month Day,] Year.' reads as 'Journal. Year.'
    ref = re.sub(r"\.\s+Published online(?: ahead of print)?\s+(?:[A-Z][a-z]{2,8}\.?\s+(?:\d{1,2},\s+)?)?"
                 r"(?=(?:19|20)\d{2}\b)", ". ", ref)
    ref = _join_spaced_urls(ref)
    rec = _blank(as_written)

    md = DOI_RE.search(ref)
    if md:
        d = md.group(1).rstrip(".,;]")
        while d.endswith(")") and d.count("(") < d.count(")"):
            d = d[:-1]                       # a closing bracket of the sentence, not of the DOI
        rec["doi"] = d.rstrip(".,;")
    mp = PMID_RE.search(ref)
    if mp:
        rec["pmid_hint"] = mp.group(1)

    # Work on the text before any DOI / URL / PMID / Epub trailer, without notes.
    core = NOTE_BRACKETS.sub("", ref)
    core = re.split(r"(?:^|\s)(?:doi\s*:|https?://|\(?PMID\s*:?|Available\s+(?:from|at)|Epub\s+\d|"
                    r"Accessed\s+\d|\(accessed|PMCID\s*:|NIHMSID\s*:|(?:Erratum|Comment|Retraction|Update|"
                    r"Expression\s+of\s+concern|Corrected\s+and\s+republished|Republished)\s+(?:in|of|on)\s*:)",
                    core, maxsplit=1, flags=re.I)[0].rstrip(" .,;")

    if not _parse_core(core, rec) and _AUTHOR_LIST_ONLY.match(core):
        rec["authors"] = core.rstrip(". ")             # 'Smith J, et al. doi:10...': authors and an identifier
        rec["parse_method"] = "identifier_only"
    elif not rec["parse_method"]:
        parts = re.split(r"(?<=[.?!])\s+", core)
        rec["authors"] = parts[0].rstrip(".") if len(parts) > 1 else ""
        rec["title"] = (parts[1] if len(parts) > 1 else core).rstrip(".")
        rec["year"] = extract_year(core)
        rec["parse_note"] = "unrecognised style; title is a guess"
        rec["parse_method"] = "fallback"
    if not rec["year"]:
        rec["year"] = extract_year(re.sub(r"(?:https?://[^\s()]+|" + DOI_RE.pattern + r")", " ", ref, flags=re.I))
    _split_et_al_title(rec)
    _try_other_styles(core, rec)
    # 'Organisation. Title. 2024. Available from: https://...': an organisation has no initials, so
    # the reader took it for the title and the title for a journal. A web page has no journal.
    if (not rec["authors"] and rec["title"] and rec["journal"] and _WEB_SOURCE.search(text)
            and not re.search(r"\d", rec["journal"])):
        rec["authors"], rec["title"], rec["journal"] = rec["title"], rec["journal"], ""
    rec["confidence"] = field_confidence(rec)
    return rec


# "Smith, J, Jones, K, et al, Title ..." or "Smith J, et al., Title": the author list ends with
# "et al" followed by a comma, so no full stop separates it from the title and no authors were
# found. Found in the live benchmark (case C16-023): the first author was missing and the title
# carried the author list, so a correct PMID could not be confirmed.
_ET_AL_TITLE = re.compile(r"^(?P<au>[A-Z](?:[^.]|(?<=\b[A-Z])\.){0,500}?\bet\.?\s*al\b\.?)\s*[,;:]\s*"
                          r"(?P<ti>\S.{9,})$")


def _split_et_al_title(rec):
    if rec.get("authors") or not rec.get("title"):
        return
    m = _ET_AL_TITLE.match(rec["title"])
    if not m or "," not in m.group("au"):
        return
    names = [x.strip() for x in re.split(r"[,;]", re.sub(r"\bet\.?\s*al\b\.?", "", m.group("au")))]
    # every piece must look like a name: capitalised words (with particles) or initials
    particle = {"van", "von", "de", "der", "den", "del", "da", "di", "le", "la", "du", "dos", "y", "e"}
    for x in filter(None, names):
        words = x.replace(".", " ").split()
        if not words or len(words) > 4 or not all(w[0].isupper() or w.lower() in particle for w in words):
            return
    rec["authors"] = m.group("au").rstrip(" .,")
    rec["title"] = m.group("ti").strip()
    rec["parse_note"] = (rec.get("parse_note", "") + "; " if rec.get("parse_note") else "") + \
        "authors end with 'et al' and a comma; split there"


_METHOD_LEVEL = {"identifier_only": "medium", "vancouver": "high", "endnote_country_year": "high", "paren_year": "medium",
                 "apa": "medium", "loose_year": "medium", "fallback": "low", "": "low"}


def field_confidence(rec):
    """Heuristic confidence per field: high, medium, low or missing.

    These are parsing heuristics, not measured accuracies."""
    base = _METHOD_LEVEL.get(rec.get("parse_method", ""), "low")
    if rec.get("parse_note"):
        base = "low"
    down = {"high": "medium", "medium": "low", "low": "low"}
    out = {}
    for k in ("authors", "title", "journal", "year", "volume", "issue", "pages", "doi"):
        v = str(rec.get(k) or "")
        if not v:
            out[k] = "missing"
            continue
        c = base
        if k in ("doi",) and re.fullmatch(r"10\.\d{4,9}/\S+", v):
            c = "high"
        elif k == "year" and re.fullmatch(r"(?:19|20)\d{2}", v):
            c = "high" if base != "low" else "medium"
        elif k == "title" and (len(v) < 20 or len(v) > 400):
            c = down[c]
        elif k == "authors" and not re.search(r"[A-Z][a-z]+", v):
            c = down[c]
        elif k == "journal" and len(v) > 120:
            c = "low"
        out[k] = c
    return out


# ------------------------------------------------------ list detection ------

def _heading_re(custom):
    if custom:
        body = r"\s+".join(map(re.escape, custom.split()))
        return re.compile(r"^\s*(?:#{1,6}\s*)?(?:\d+[.)]?\s*)?" + body + r"\s*:?\s*$", re.I)
    return re.compile(r"^\s*(?:#{1,6}\s*)?(?:\d+[.)]?\s*)?(?:" + DEFAULT_HEADINGS + r")\s*:?\s*$", re.I)


_INVISIBLE = re.compile("[\u00ad\u200b\u200c\u200d\u2060\ufeff]")
_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
              "\ufb05": "st", "\ufb06": "st"}


def clean_invisible(s):
    """Drop soft hyphens and zero width characters; spell out ligatures (U+FB01 'fi'). Text copied
    from a PDF or a web page carries them; they break title and name comparison."""
    s = _INVISIBLE.sub("", s)
    return re.sub("[\ufb00-\ufb06]", lambda m: _LIGATURES[m.group(0)], s)


_DOI_BROKEN = re.compile(r"(?:doi\.org/|doi:\s*|\b10\.\d{4,9}/)\S*[-/._]$", re.I)


def _doi_tail(cur, line):
    """'...s43588-024-00607-' then '6' on its own line: the end of a DOI, not a page number."""
    return bool(cur and _DOI_BROKEN.search(cur["text"]) and re.fullmatch(r"[\w\-./()<>:;]{1,40}", line))


_NEXT_LIST = re.compile(r"^\s*(?:#{1,6}\s*)?(?:" + DEFAULT_HEADINGS + r")\s*:?\s*$", re.I)


_AUTHOR_LIST_START = re.compile(r"(?:(?:van|von|de|der|den|del|da|di|du|le|la)\s)*[A-Z\u00C0-\u00DE][\w'\u2019\-]+,?\s"
                                r"[A-Z]{1,4}[,.\s]")
BULLET = re.compile(r"^\s*[\u2022\u25e6\u25aa\u25ab\u25cf\u25cb\u25a0\u25a1\u2023\u2043\u2219\u00b7\uf0b7]\s*")


def _split_runs_together(items):
    """'15. Smith J ... 2020;1:2-3. 16. Brown A ...' in one paragraph, when 16 is missing from the
    list: two references typed into one paragraph. Split where the missing number starts an author."""
    out = []
    for k, it in enumerate(items):
        nxt = items[k + 1]["num"] if k + 1 < len(items) else None
        want = it["num"] + 1
        m = None
        if nxt is None or nxt > want:
            m = re.search(r"(?<=[.)\]])\s+" + str(want) + r"\.\s+(?=(?:(?:van|von|de|der|den|del|da|di|du|le|la)\s)*"
                          r"[A-Z\u00C0-\u00DE][\w'\u2019\-]+,?\s[A-Z]{1,4}[,.\s])", it["text"])
        if m:
            out.append(dict(it, text=it["text"][: m.start()].strip(), split=True))
            out.append(dict(it, num=want, text=it["text"][m.end():].strip(), split=True))
        else:
            out.append(it)
    return out


def _gather(lines, start, repeats=None):
    """Collect items after `start`. Returns (items, numbering_mode)."""
    items, cur, prev = [], None, 0
    repeats = [] if repeats is None else repeats
    for text, list_item, pidx in lines[start:]:
        line = re.sub(r"\s+", " ", clean_invisible(text)).strip()
        if line and _doi_tail(cur, line) and not list_item:
            cur["text"] += line
            continue
        if not line or IGNORE_LINE.match(line):
            continue
        if (STOP_HEADING.match(line) or _NEXT_LIST.match(line)) and not NUM_START.match(line):
            break                             # the next section, or the next chapter's reference list
        bullet = BULLET.match(line)
        if bullet and cur is not None:
            # a bulleted item ('• Smith J, ...'): a new reference with the next number
            items.append(cur)
            prev += 1
            cur = {"num": prev, "text": line[bullet.end():].strip(), "para_start": pidx, "para_end": pidx}
            continue
        m = NUM_START.match(line)
        if m and cur is not None and _num(m) == prev and (
                pidx != cur["para_start"] or (pidx is None and _AUTHOR_LIST_START.match(line[m.end():]))):
            # the same number typed twice, at the start of a new paragraph or line: the next reference
            repeats.append(prev)
            items.append(cur)
            prev += 1
            cur = {"num": prev, "text": line[m.end():].strip(), "para_start": pidx, "para_end": pidx}
            continue
        if not m and not list_item:
            # '1<tab>Smith J' (EndNote numbered styles with a tab and no full stop): a bare number
            # starts a reference only as the next in the sequence
            b = NUM_BARE.match(line)
            if b and int(b.group(1)) == (prev + 1 if cur is not None else 1):
                m = b
        typed_ok = m and (cur is None or 0 < _num(m) - prev <= 3)
        if typed_ok or list_item:
            if cur:
                items.append(cur)
            num = _num(m) if typed_ok else prev + 1
            cur = {"num": num, "text": line[m.end():].strip() if typed_ok else line,
                   "para_start": pidx, "para_end": pidx}
            prev = num
        elif cur is not None:
            # a word, name or page range broken at a line end ('Next-' / 'Generation', 'Reina-' /
            # 'Ortiz', '610-' / '4') joins without a space and keeps its hyphen
            cur["text"] += ("" if re.search(r"\w[-\u2010\u2011\u2013]$", cur["text"]) and line[:1].isalnum()
                            else " ") + line
            cur["para_end"] = pidx if pidx is not None else cur["para_end"]
    if cur:
        items.append(cur)
    return _split_runs_together(items)


# where an unnumbered reference starts on a printed line: 'Kok, C. H.,' 'Emmert-Streib, F.,' 'van de Donk,
# N. W.' 'Smith J, ' 'Smith JA.' 'World Health Organization. (2020).'
AUTHOR_START = re.compile(
    r"^(?:(?:van|von|de|der|den|del|da|di|du|le|la|dos|das|ten|ter)\s+){0,3}[A-Z\u00c0-\u00de][\w'\u2019\-]+"
    r"(?:\s[A-Z\u00c0-\u00de][\w'\u2019\-]+)?"
    r"(?:,\s*(?:[A-Z\u00c0-\u00de]\.|[A-Z]{1,3}[,.])|\s[A-Z]{1,3}[,.]|(?:\s[A-Z][a-z]+)*\.\s\((?:19|20)\d{2}[a-z]?\))")
_ENDS_REF = re.compile(r"(?:[.)]|https?://\S+|doi:\s*\S+|10\.\d{4,9}/\S+)$", re.I)


def _gather_unnumbered(lines, start):
    """Author year lists. Word paragraphs: one paragraph = one reference. Printed lines (PDF,
    text): a reference starts at a line that opens with an author name when the text before it
    ends a reference (a full stop, a closing bracket, a DOI or URL); other lines continue it."""
    items, n = [], 0
    for text, _, pidx in lines[start:]:
        line = re.sub(r"\s+", " ", clean_invisible(text)).strip()
        cur = items[-1] if items else None
        if line and BULLET.match(line):
            n += 1
            items.append({"num": n, "text": line[BULLET.match(line).end():].strip(), "para_start": pidx,
                          "para_end": pidx})
            continue
        if pidx is None and line and _doi_tail(cur, line):
            cur["text"] += line
            continue
        if not line or IGNORE_LINE.match(line):
            continue
        if STOP_HEADING.match(line):
            break
        if pidx is None and cur and not (AUTHOR_START.match(line) and _ENDS_REF.search(cur["text"])):
            cur["text"] += ("" if re.search(r"[\w/][-\u2010\u2011\u2013]$", cur["text"]) and line[:1].isalnum()
                            else " ") + line
            continue
        n += 1
        items.append({"num": n, "text": line, "para_start": pidx, "para_end": pidx})
    return items


def find_reference_list(lines, heading=None):
    """Return (items, heading_line_index or None, warnings)."""
    warnings = []
    hre = _heading_re(heading)
    cands = [i for i, (t, _, _) in enumerate(lines) if hre.match(re.sub(r"\s+", " ", t).strip())]
    best, best_h = [], None
    repeats = {}
    for h in cands:
        rep = []
        items = _gather(lines, h + 1, rep)
        repeats[h] = rep
        if len(items) >= len(best):
            best, best_h = items, h
    if cands and len(best) < 3:
        # Heading found but (almost) nothing numbered: an author year list. A stray number such as
        # the end of a DOI wrapped onto its own line is not a numbered list.
        h = cands[-1]
        unnum = _gather_unnumbered(lines, h + 1)
        if len(unnum) > max(len(best), 2) or (unnum and not best):
            best, best_h = unnum, h
            printed = all(p is None for _, _, p in lines)
            warnings.append("Reference list is not numbered; " + (
                "a reference was taken to start at each line that opens with an author name."
                if printed else "each paragraph was treated as one reference."))
    if not cands:
        items = _gather(lines, 0)
        nonempty = sum(1 for t, _, _ in lines if t.strip())
        if items and len(items) >= max(2, nonempty // 3):
            best = items
            warnings.append("No reference heading found; the whole file was read as a reference list.")
        else:
            raise ValueError(
                'No "References" heading found. Enter the section title used in your '
                "document (for example Bibliography)."
            )
    # A thesis or a book has one numbered list per chapter: name the others, so the user can check
    # each chapter on its own, and tell the converter where this list's chapter starts
    if best_h is not None:
        others = []
        for h in cands:
            if h != best_h:
                items = _gather(lines, h + 1)
                if len(items) >= 3:
                    others.append((h, items))
        if others:
            warnings.append(
                f"This document has {len(others) + 1} reference lists (one per chapter?). RefCheck checked "
                f"the one with {len(best)} references and converted only the citations between it and the "
                "list before it. Check each chapter as a file of its own.")
            before = [it["para_end"] for h, items in others if h < best_h for it in items
                      if it.get("para_end") is not None]
            if before:
                warnings.append(f"__body_start__{max(before) + 1}")
    for n in repeats.get(best_h, []):
        warnings.append(f"Number {n} is used twice in the reference list; the second was read as {n + 1}. "
                        "Check the numbering against the citations.")
    split = sorted({it["num"] for it in best if it.get("split")})
    if split:
        warnings.append("References " + ", ".join(map(str, split)) + " were typed in one paragraph and read "
                        "as separate references.")
    # Gaps / repeats in numbering
    nums = [it["num"] for it in best]
    for a, b in zip(nums, nums[1:]):
        if b != a + 1:
            warnings.append(f"Numbering jumps from {a} to {b}; check that reference {a} was not merged with the next one.")
    return best, best_h, warnings


# ---------------------------------------------------------- table input -----

def _find_col(headers, names):
    norm = {clean_text(h): h for h in headers}
    for n in names:
        if clean_text(n) in norm:
            return norm[clean_text(n)]
    return None


def refs_from_table(rows):
    if not rows:
        raise ValueError("The table is empty.")
    h = list(rows[0].keys())
    col = {k: _find_col(h, v) for k, v in {
        "num": ["reference_number", "ref_no", "ref", "number", "no", "#", "id"],
        "text": ["original_reference", "citation", "reference", "reference_text",
                 "full_reference", "bibliography", "citation_text"],
        "title": ["title", "article_title"], "authors": ["authors", "author", "author_list"],
        "journal": ["journal", "journal_name", "source"], "year": ["year", "publication_year", "pub_year"],
        "volume": ["volume", "vol"], "issue": ["issue"], "pages": ["pages", "page", "pagination"],
        "doi": ["doi"], "pmid_hint": ["pmid", "pubmed_id"],
    }.items()}
    if not col["text"] and not col["title"]:
        # Fall back to the column with the longest average text.
        col["text"] = max(h, key=lambda c: sum(len(str(r.get(c) or "")) for r in rows))
    out = []
    for i, row in enumerate(rows, 1):
        get = lambda k: str(row.get(col[k]) or "").strip() if col[k] else ""
        text = get("text")
        rec = parse_reference_text(text) if text else _blank("")
        # Explicit columns win over parsed values, except where the parsed
        # citation is more reliable (title polluted by the journal name).
        for k in ("title", "authors", "journal", "volume", "issue", "pages", "doi", "pmid_hint"):
            v = get(k)
            if v and not (k == "title" and rec["title"] and rec["journal"] and v.endswith(rec["journal"])):
                rec[k] = v
        rec["year"] = rec["year"] or extract_year(get("year"))
        if not text:
            rec["text"] = ". ".join(x for x in (rec["authors"], rec["title"], rec["journal"], rec["year"]) if x)
        n = get("num")
        rec["num"] = int(n) if n.isdigit() else i
        out.append(rec)
    return out


# ------------------------------------------------------------ LaTeX input ---

def _latex_references(path, ext):
    from .latexbib import bib_files_named, bibitems, has_thebibliography, refs_from_bibtex
    src = Path(path).read_text(encoding="utf-8", errors="replace")
    warnings = []
    if ext == ".bib":
        refs = refs_from_bibtex(src, _blank)
        if not refs:
            raise ValueError("No BibTeX entries were found in this file.")
        warnings.append("BibTeX input: entries are numbered in file order, which may differ from the order "
                        "they are cited in the paper.")
        return dict(refs=refs, source_kind="bibtex", heading_para=None, warnings=warnings, docx_path=None)
    if not has_thebibliography(src):
        names = bib_files_named(src)
        raise ValueError("This file has no \\bibitem entries. "
                         + (f"It takes its references from {', '.join(names)}: upload that .bib file, or the "
                            "compiled .bbl file." if names else "Upload the .bib or the compiled .bbl file."))
    refs = []
    for n, it in enumerate(bibitems(src), 1):
        rec = parse_reference_text(it["text"])
        rec.update(num=n, bibtex_key=it["key"])
        refs.append(rec)
    return dict(refs=refs, source_kind="bbl" if ext == ".bbl" else "tex", heading_para=None,
                warnings=warnings, docx_path=None)


# ------------------------------------------------------------ public API ----

def extract_references(path, heading=None, workdir=None, grobid_url=None, grobid_allow_remote=False):
    """Returns dict(refs, source_kind, heading_para, warnings, docx_path).

    workdir: folder for a LibreOffice conversion (.doc, .odt, .rtf). The caller owns
    its lifetime; without it a temporary folder is used and removed at exit."""
    path = Path(path)
    ext = path.suffix.lower()
    warnings, docx_path, heading_para = [], None, None

    if ext == ".pdf" and grobid_url:
        from .grobid import grobid_references
        refs = grobid_references(path, grobid_url, allow_remote=grobid_allow_remote)
        return dict(refs=refs, source_kind="pdf (GROBID)", heading_para=None,
                    warnings=[] if refs else ["GROBID found no references."], docx_path=None)

    if ext in TABLE_EXT:
        refs = refs_from_table(read_table(path))
        return dict(refs=refs, source_kind="table", heading_para=None, warnings=warnings, docx_path=None)

    if ext in LATEX_EXT:
        return _latex_references(path, ext)

    if ext in (".doc", ".odt") or (ext == ".rtf" and (shutil.which("soffice") or shutil.which("libreoffice"))):
        path = _convert_with_soffice(path, workdir)
        ext = ".docx"

    if ext == ".docx":
        lines, docx_path = read_docx_lines(path, heading), path
    elif ext == ".pdf":
        lines = read_pdf_lines(path)
    else:
        lines = read_text_lines(path)

    items, h, w = find_reference_list(lines, heading)
    body_start = next((int(x[len("__body_start__"):]) for x in w if x.startswith("__body_start__")), None)
    warnings += [x for x in w if not x.startswith("__body_start__")]
    if ext == ".docx" and h is not None:
        heading_para = lines[h][2]

    refs = []
    for it in items:
        rec = parse_reference_text(it["text"])
        rec.update(num=it["num"], para_start=it["para_start"], para_end=it["para_end"])
        refs.append(rec)
    kind = "docx" if ext == ".docx" else ext.lstrip(".")
    return dict(refs=refs, source_kind=kind, heading_para=heading_para,
                warnings=warnings, docx_path=docx_path, body_start_para=body_start)



# ----------------------------------------------------------- other styles ----
# Nature, Science, Cell, Frontiers, Harvard, Chicago, IEEE, MLA and older PLOS. Each reader
# returns a full set of fields or nothing. The parse that reads the most fields plausibly is
# kept, so a reference the Vancouver readers already read completely is left alone.

_PG = r"[A-Za-z]{0,4}\d+[A-Za-z]?(?:\s*[\u2013\u2014\-]\s*[A-Za-z]{0,4}\d+[A-Za-z]?)?(?:\.e\d+)?"
_YR = r"(?:19|20)\d{2}"
# surname particles written in lower case: 'van den Bent', 'de Vries', 'von Lilienfeld-Toal'
_PARTICLE = r"(?:(?:van|von|den|der|de|del|della|di|da|du|dos|das|la|le|ter|ten|zu|y)\s)"
_SURNAME = (_PARTICLE + r"*[A-Z\u00C0-\u00DE][\w'\u2019\-]+(?:\s" + _PARTICLE + r"*"
            r"[A-Z\u00C0-\u00DE][\w'\u2019\-]+)*")
# 'Li C-I', 'Chung T-H', 'Bendaña López Á': surname then capital initials without full stops
_NAME_VAN = r"[A-Z\u00C0-\u00DE][\w'\u2019\-]+(?:\s[A-Z\u00C0-\u00DE][\w'\u2019\-]+)?\s[A-Z\u00C0-\u00DE]{1,3}(?:-[A-Z\u00C0-\u00DE]{1,2})?"
_NAME_IF = (r"(?:(?:[A-Z\u00C0-\u00DE]\.(?:\s?-?[A-Z\u00C0-\u00DE]\.)*\s)" + _PARTICLE + r"*[A-Z\u00C0-\u00DE][\w'\u2019\-]+"
            r"(?:\s[A-Z][\w'\u2019\-]+)?|" + _NAME_VAN + r"(?=,))")
_NAME_SI = (r"(?:" + _SURNAME + r",\s(?:[A-Z\u00C0-\u00DE][a-z]?\.(?:\s?-?[A-Z\u00C0-\u00DE][a-z]?\.)*)|"
            + _NAME_VAN + r"(?=,))")
_NATURE_TAIL = re.compile(r"(?:\s(?P<vol>[A-Za-z]?\d+[A-Za-z]?)(?:\s*\((?P<iss>[^)]*)\))?(?:,\s*(?P<pg>" + _PG +
                          r"))?,?)?\s*\((?P<yr>" + _YR + r")\)\.?$")
_QUOTED = re.compile(r"(?:^|(?<=\s))(?:\u201c(?P<a>[^\u201d]{15,}?)\u201d|\"(?P<b>[^\"]{15,}?)\"|"
                     r"\u2018(?P<c>.{15,}?)\u2019(?=[,.]|\s|$)|'(?P<d>.{15,}?)'(?=[,.]|\s|$))")


def _fields_score(r):
    """How completely and plausibly a parse reads a reference (higher is better)."""
    sc = 0
    t = r.get("title", "")
    tail = re.split(r"[.?!]\s", t)[-1] if re.search(r"[.?!]\s", t) else ""
    if tail and len(tail.split()) <= 5 and all(re.fullmatch(r"[A-Z][A-Za-z]{0,5}\.?", w) for w in tail.split()):
        t = ""                                  # 'Title. N. Engl. J': the journal left in the title
    if 15 <= len(t) and not re.search(r"\bvol\.|\bpp?\.\s*\d|[\u201c\u201d]|\(\d{4}\)|^(?:&|and\s)|,\s[A-Z]\.\s", t):
        sc += 3
    if r.get("authors") and first_author_surname(r["authors"]):
        sc += 2
    j = r.get("journal", "")
    if j and not re.search(r"\d", j) and len(j) <= 80:
        sc += 2
    sc += bool(re.fullmatch(_YR, r.get("year", "")))
    sc += bool(re.fullmatch(r"[A-Za-z]?\d{1,5}[A-Za-z]?", r.get("volume", "")))
    sc += bool(re.fullmatch(_PG.replace("\\s*", ""), r.get("pages", "").replace(" ", "")) or
               re.fullmatch(_PG, r.get("pages", "")))
    return sc


# Abbreviated words of ISO 4 journal titles (LTWA): a period after one of these, or after a
# single capital letter, is inside the journal title, not the end of the article title.
_ISO_WORDS = set("""Acad Acta Adv Afr Am Anal Anat Ann Anesth Annu Appl Arch Assoc Bioinform Biochem Biol
Biomed Biophys Biotechnol Br Bull Can Carcinog Cardiol Cell Chem Chim Chin Clin Commun Comp Comput Crit
Curr Cytogenet Dermatol Dev Diagn Dis Drug Ecol Endocrinol Engl Environ Epidemiol Eur Exp Fam Gastroenterol
Gen Genet Geriatr Gynecol Haematol Hematol Hepatol Hist Hosp Hum Immunol Infect Inst Int Intern Invest Ital
Jpn Lab Lett Leuk Mater Math Mech Med Metab Methods Microbiol Mol Nat Natl Nephrol Neurol Neurosci Nucl
Nutr Obstet Oncol Ophthalmol Opin Orthop Otolaryngol Paediatr Pathol Pediatr Pharm Pharmacol Philos Phys
Physiol Proc Prog Psychiatr Psychiatry Psychol Pract Prev Pulm Q Radiol Rep Res Respir Rev Rheumatol Sci
Semin Soc Stat Surg Syst Technol Ther Toxicol Transl Transplant Trop Urol Vasc Vet Virol Z
Agric Allergy Antimicrob Antivir Behav Bioeng Bioinform Biomater Biomol Bioorg Cardiovasc Cogn Cytom Diabetol
Emerg Endocr Entomol Evol Front Genom Glob Hered Histochem Hyg Immunother Ind Inf Inorg Integr Mar
Med Microsc Mutat Nanomed Nanotechnol Neuropathol Neuropsychol Neuropsychopharmacol Nurs Oncogenes
Pathog Pharmaceut Phys Polym Regen Rehabil Reprod Rev Sens Struct Sustain
Theor Vaccin Vis Zool""".split())


def _abbr_word(w):
    return bool(re.fullmatch(r"[A-Z\u00C0-\u00DE]", w)) or w in _ISO_WORDS


def _title_journal(rest):
    """Split 'Title. Journal' (the volume, pages and year already removed): the title ends at
    the last full stop that does not follow an abbreviated journal word."""
    rest = rest.strip()
    cuts = [m for m in re.finditer(r"(?P<w>[\w\u00C0-\u024F'\u2019\-]*)(?P<p>[.?!])\s+(?=\S)", rest)]
    for m in reversed(cuts):
        if m.group("p") != "." or not _abbr_word(m.group("w")):
            end = m.end("p") if m.group("p") in "?!" else m.start("p")
            return rest[:end].strip(), rest[m.end():].strip(" .,")
    return "", rest.strip(" .,")


def _tail_fields(after, r):
    """Journal, volume, issue, pages and year after a title, for labelled and unlabelled tails:
    ', Journal, vol. 15, no. 1, pp. 42-50, 2021.'  ', Journal, 15(1), pp. 42-50.'
    '. Journal 15 (1): 3937.'  '. Journal, vol. 15, no. 1, 2024, p. 3937.'"""
    a = after.strip(" ,.;")
    vol = re.search(r"\bvol\.?\s*([A-Za-z]?\d+[A-Za-z]?)", a, re.I)
    if vol:
        r["journal"] = a[: vol.start()].strip(" ,.;")
        r["volume"] = vol.group(1)
        iss = re.search(r"\bno\.?\s*([\w\-]+)", a, re.I)
        r["issue"] = iss.group(1) if iss else ""
        pg = re.search(r"\bpp?\.\s*(" + _PG + ")", a)
        r["pages"] = norm_dashes(pg.group(1)).replace(" ", "") if pg else ""
        rest = a[vol.end():]
        if pg:
            rest = rest.replace(pg.group(0), " ")
        y = re.findall(r"\b" + _YR + r"\b", rest)
        if y:
            r["year"] = y[-1]
        return True
    m = re.match(r"^(?P<j>[^,\d][^\d]*?),\s*(?P<vol>[A-Za-z]?\d+[A-Za-z]?)\s*(?:\((?P<iss>[^)]*)\))?,?\s*(?:pp?\.\s*)?(?P<pg>" +
                 _PG + r")?", a)
    if m and not re.search(r"\d", m.group("j")):
        r["journal"], r["volume"], r["issue"] = m.group("j").strip(" ."), m.group("vol"), m.group("iss") or ""
        r["pages"] = norm_dashes(m.group("pg") or "").replace(" ", "")
        return True
    m = re.match(r"^(?P<j>.+?)\s+(?P<vol>[A-Za-z]?\d+[A-Za-z]?)\s*(?:\((?P<iss>[^)]*)\))?\s*(?::\s*(?P<pg>" + _PG + r"))?$", a)
    if m and not re.search(r"\d", m.group("j")):
        r["journal"], r["volume"], r["issue"] = m.group("j").strip(" .,"), m.group("vol"), m.group("iss") or ""
        r["pages"] = norm_dashes(m.group("pg") or "").replace(" ", "")
        return True
    if a and not re.search(r"\d", a) and len(a) <= 80:
        r["journal"] = a                        # online only: 'Blood.'
        return True
    return False


def _quoted_title_style(core):
    """Harvard, Chicago, IEEE, MLA: the title is in quotation marks."""
    m = _QUOTED.search(core)
    if not m or m.start() < 3:
        return None
    r = _blank(core)
    before = core[: m.start()]
    y = re.search(r"\((" + _YR + r")[a-z]?\)|(?:^|[.,]\s)(" + _YR + r")[a-z]?\.\s*$", before)
    if y:
        r["year"] = y.group(1) or y.group(2)
        before = before[: y.start()]
    r["authors"] = before.strip(" ,.;")
    r["title"] = next(g for g in m.groups() if g).strip(" ,.")
    _tail_fields(core[m.end():], r)
    if not r["year"]:
        ys = re.findall(r"\b(" + _YR + r")\b", core[m.end():])
        r["year"] = ys[-1] if ys else ""
    r["parse_method"] = "quoted_title"
    return r


def _nature_style(core):
    """Nature: 'Authors. Title. Journal 15, 3937 (2024).'  Science: 'A. Name, B. Name, Title.
    Journal 15, 3937 (2024).'"""
    m = _NATURE_TAIL.search(core)
    if not m:
        return None
    r = _blank(core)
    r["year"], r["volume"], r["issue"] = m.group("yr"), m.group("vol") or "", m.group("iss") or ""
    r["pages"] = norm_dashes(m.group("pg") or "").replace(" ", "")
    pre = core[: m.start()].strip()
    sci = re.match(r"^(?P<au>" + _NAME_IF + r"(?:,\s(?:and\s)?" + _NAME_IF + r")*(?:,?\set\sal\.)?),\s+(?P<rest>.+)$", pre)
    nat = re.match(r"^(?P<au>" + _NAME_SI + r"(?:(?:,|,?\s&)\s" + _NAME_SI + r")*(?:,?\set\sal\.)?)\s+(?P<rest>[^\s].+)$", pre)
    if sci and nat and len(nat.group("au")) > len(sci.group("au")):
        sci = None                     # both fit: the reading with the longer author list wins
    if sci:
        r["authors"], rest, r["parse_method"] = sci.group("au").strip(" ,"), sci.group("rest"), "science"
    elif nat:
        r["authors"], rest, r["parse_method"] = nat.group("au").strip(" ,"), nat.group("rest"), "nature"
    else:
        return None
    r["title"], r["journal"] = _title_journal(rest)
    return r if r["title"] else None


def _author_year_style(core):
    """Cell, Frontiers, older PLOS: 'Authors (2024). Title. Journal 15, 3937.' or
    'Authors (2013) Title. Journal 368: 2059-2074.'"""
    m = re.match(r"^(?P<au>.+?)\s*\((?P<yr>" + _YR + r")[a-z]?\)\.?\s+(?P<rest>.+)$", core)
    if not m:
        return None
    r = _blank(core)
    r["authors"], r["year"] = m.group("au").strip(" ,."), m.group("yr")
    rest = m.group("rest").strip()
    t = re.search(r"\s(?P<vol>[A-Za-z]?\d+[A-Za-z]?)(?:\s*\((?P<iss>[^)]*)\))?(?:\s*[,:]\s*(?P<pg>" + _PG + r"))?\.?$", rest)
    r["title"], r["journal"] = _title_journal(rest[: t.start()] if t else rest)
    if not r["title"] or re.search(r"\d", r["journal"]):
        return None
    if t:
        r["volume"], r["issue"] = t.group("vol"), t.group("iss") or ""
        r["pages"] = norm_dashes(t.group("pg") or "").replace(" ", "")
    r["parse_method"] = "author_year"
    return r


_MDPI_TAIL = re.compile(r"\s(?P<yr>" + _YR + r"),\s*(?P<vol>[A-Za-z]?\d+[A-Za-z]?)(?:\s*\((?P<iss>[^)]*)\))?"
                        r"(?:,\s*(?P<pg>" + _PG + r"))?\.?$")


def _mdpi_style(core):
    """MDPI: 'Ley, T.J.; Miller, C.; Ding, L. Title. N. Engl. J. Med. 2013, 368, 2059–2074.'"""
    m = _MDPI_TAIL.search(core)
    if not m:
        return None
    pre = re.sub(r"^\s*(?:\[\d+\]|\d+[.)])\s*", "", core[: m.start()]).strip()
    items = pre.split("; ")
    # the list ends at the final author's initials: '...; Pérez, M. Patient specific ...'
    last = re.match(r"^(?P<a>[^,;]{1,60},\s(?:[^\W\d_]{1,2}\.(?:\s?-?[^\W\d_]{1,2}\.)*)(?:,?\set\sal\.)?)\s+(?P<rest>\S.+)$",
                    items[-1])
    if not last or (len(items) == 1 and not re.match(_NAME_SI, pre)):
        return None
    r = _blank(core)
    r["authors"] = "; ".join(items[:-1] + [last.group("a")])
    r["title"], r["journal"] = _title_journal(last.group("rest"))
    if not r["title"] or re.search(r"\d", r["journal"]):
        return None
    r["year"], r["volume"], r["issue"] = m.group("yr"), m.group("vol"), m.group("iss") or ""
    r["pages"] = norm_dashes(m.group("pg") or "").replace(" ", "")
    r["parse_method"] = "mdpi"
    return r


def _try_other_styles(core, rec):
    """Replace the parse with another style's reading when that reads clearly more fields."""
    best, best_sc = None, _fields_score(rec)
    for reader in (_quoted_title_style, _nature_style, _author_year_style, _mdpi_style):
        try:
            alt = reader(core)
        except re.error:                       # pragma: no cover
            alt = None
        if alt is None:
            continue
        sc = _fields_score(alt)
        if sc > best_sc:
            best, best_sc = alt, sc
    if best is not None:
        for k in ("authors", "title", "journal", "year", "volume", "issue", "pages", "parse_method"):
            rec[k] = best[k]
        rec["parse_note"] = ""
