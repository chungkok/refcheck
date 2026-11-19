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
import subprocess
import tempfile
from pathlib import Path

from .textutil import DASH_RE, clean_text, extract_year, norm_dashes

TEXT_EXT = {".txt", ".md", ".text", ".markdown"}
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xlsm"}


# --------------------------------------------------------------- regexes ----

DEFAULT_HEADINGS = (
    r"references?(\s+list)?|bibliography|literature\s+cited|works\s+cited|"
    r"cited\s+literature|reference\s+list"
)

STOP_HEADING = re.compile(
    r"^\s*(tables?\s+(&|and)\s+figures?|figure\s+legends?|figures?\b|tables?\b|"
    r"appendix|appendices|supplementar(?:y|ies|ily)\b|acknowledg(e)?ments?|"
    r"funding\b|conflicts?\s+of\s+interest|competing\s+interests?|declarations?\b|"
    r"author\s+contributions?|data\s+availability|abbreviations\b|"
    r"publisher[\u2019']?s\s+note|authors?\s+and\s+affiliations|author\s+information|"
    r"additional\s+information|ethics\s+declarations|rights\s+and\s+permissions|"
    r"about\s+this\s+article)",
    re.I,
)

# "1. Smith"  "1) Smith"  "[1] Smith"  "[1]Smith"; not "10.3389/..." (a wrapped DOI)
NUM_START = re.compile(r"^\s*(?:\[(\d{1,4})\]\s*|(\d{1,4})\s*[.)]\s+)(?=\S)")


def _num(m):
    return int(m.group(1) or m.group(2))
IGNORE_LINE = re.compile(
    r"^\s*(official|confidential|page\s+\d+(\s+of\s+\d+)?|\d{1,4})\s*$", re.I
)

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# "Journal. 2021;23(8):1231-1251."  "Blood. 2021 Jan 7;137(1):1."
# "medRxiv. 2026. doi:..."  "Lancet. 2020. [Epub ahead of print]"
YEAR_ANCHOR = re.compile(
    r"(?<=[.?!] )(?:19|20)\d{2}[a-z]?"
    r"(?:\s+" + _MONTH + r"(?:\s*" + DASH_RE + r"\s*" + _MONTH + r")?(?:\s+\d{1,2})?)?"
    r"(?=\s*;|\s*:\s*\d|\s*\.\s*(?:doi\b|https?:|epub\b|pmid\b|\[|published\b)"
    r"|\s*\.?\s*$|\s*\.\s*\d+\s*\(|\s*,\s*\d)",
    re.I,
)
APA_RE = re.compile(
    r"^(?P<authors>.+?)\s*\((?P<year>(?:19|20)\d{2})[a-z]?(?:,[^)]*)?\)\.\s*(?P<rest>.+)$"
)
DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"<>]+)", re.I)
PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d{5,9})\b", re.I)


# --------------------------------------------------------------- readers ----

def _convert_with_soffice(path):
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise RuntimeError(
            f"Cannot read {Path(path).suffix} files without LibreOffice. "
            "Save the file as .docx (Word: File > Save As) and try again."
        )
    out = tempfile.mkdtemp(prefix="refcheck_")
    subprocess.run([exe, "--headless", "--convert-to", "docx", "--outdir", out, str(path)],
                   check=True, capture_output=True, timeout=180)
    conv = Path(out) / (Path(path).stem + ".docx")
    if not conv.exists():
        raise RuntimeError("LibreOffice conversion failed.")
    return conv


def read_docx_lines(path):
    """[(text, starts_numbered_item, para_index)] using python-docx.

    Paragraph indices refer to Document.paragraphs (top level paragraphs), which
    the annotator uses to remove the reference list."""
    from docx import Document

    doc = Document(str(path))

    def style_numbered(style):
        while style is not None:
            ppr = style.element.pPr
            if ppr is not None and ppr.numPr is not None:
                return True
            style = style.base_style
        return False

    out = []
    for i, p in enumerate(doc.paragraphs):
        pPr = p._p.pPr
        numbered = (pPr is not None and pPr.numPr is not None) or style_numbered(p.style)
        lines = p.text.split("\n")
        for k, ln in enumerate(lines):
            out.append((ln, numbered and k == 0, i))
    return out


def read_pdf_lines(path):
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("Reading PDF needs:  pip install pypdf") from e
    lines = []
    for pg in PdfReader(str(path)).pages:
        lines.extend((pg.extract_text() or "").splitlines())
    lines = strip_line_numbers(lines)
    lines = drop_repeated_lines(lines)
    return [(ln, False, None) for ln in lines]


def strip_line_numbers(lines):
    """Remove manuscript line numbers glued to the end or start of each line
    ('References516', '517 1. Smith J'). The numbering is followed as an exact
    sequence (n, n+1, ...), so only the line number itself is removed and
    documents without line numbers are left alone."""
    st = [l.strip() for l in lines]

    def has(s, v, at_end):
        t = str(v)
        if at_end:
            return s.endswith(t) and len(s) > len(t) and not s[: -len(t)].endswith(("-", "\u2013"))
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
                if has(s2, exp + skip, at_end):
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
                parse_note="", para_start=None, para_end=None)


def _parse_tail(tail, rec):
    """';27(8):2035-2046.'  ';6:e123'  ' 43(6):676-81.'  ';27(Supplement_4):iv1-iv66'"""
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


def _parse_core(core, rec):
    m = COUNTRY_YEAR.search(core)
    if m:
        rec["year"] = m.group("year")
        rec["volume"] = m.group("vol") or ""
        rec["pages"] = re.sub(r"\s+", "", norm_dashes(m.group("pg") or "")).strip(".")
        _split_prefix(core[: m.start()], rec)
        return True
    ys = list(YEAR_ANCHOR.finditer(core))
    if ys:
        ym = ys[-1]
        rec["year"] = ym.group(0)[:4]
        _split_prefix(core[: ym.start()], rec)
        _parse_tail(core[ym.end():], rec)
        return True
    ps = list(PAREN_YEAR.finditer(core))
    if ps:
        pm = ps[-1]
        rec["year"] = pm.group("year")
        _split_prefix(core[: pm.start()], rec)
        _parse_tail(core[pm.end():], rec)
        return True
    if APA_RE.match(core):
        return _parse_apa(core, rec)
    ls = list(LOOSE_YEAR.finditer(core))
    if ls:
        lm = ls[-1]
        rec["year"] = lm.group(0)[:4]
        _split_prefix(core[: lm.start()], rec)
        _parse_tail(core[lm.end():], rec)
        return True
    return False


def _parse_apa(ref, rec):
    m = APA_RE.match(ref)
    if not m:
        return False
    rec["authors"], rec["year"] = m.group("authors").strip(" ,"), m.group("year")
    rest = m.group("rest")
    b = re.search(r"[.?!](?=\s)", rest)
    if not b:
        rec["title"] = rest.strip(" .")
        return True
    rec["title"] = rest[: b.start() + (1 if rest[b.start()] in "?!" else 0)].strip()
    after = rest[b.end():].strip()
    jm = re.match(r"(?P<j>[^,]+),\s*(?P<tail>.*)$", after)
    if jm:
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


def parse_reference_text(text):
    """Split one formatted reference into fields.
    Handles Vancouver/AMA, Frontiers 'Journal (Year)', APA, styles without a period
    before the year, and EndNote's 'Journal. 15. Country(Year).' output."""
    ref = re.sub(r"\s+", " ", str(text)).strip()
    ref = re.sub(r"\s+([.,;:])", r"\1", ref)
    ref = NUM_START.sub("", ref, count=1) if NUM_START.match(ref) else ref
    ref = _join_spaced_urls(ref)
    rec = _blank(ref)

    md = DOI_RE.search(ref)
    if md:
        rec["doi"] = md.group(1).rstrip(".,;)]")
    mp = PMID_RE.search(ref)
    if mp:
        rec["pmid_hint"] = mp.group(1)

    # Work on the text before any DOI / URL / PMID / Epub trailer, without notes.
    core = NOTE_BRACKETS.sub("", ref)
    core = re.split(r"\s(?:doi\s*:|https?://|\(?PMID\s*:?|Available\s+(?:from|at)|Epub\s+\d|"
                    r"Accessed\s+\d|\(accessed)", core, maxsplit=1, flags=re.I)[0].rstrip(" .,;")

    if not _parse_core(core, rec):
        parts = re.split(r"(?<=[.?!])\s+", core)
        rec["authors"] = parts[0].rstrip(".") if len(parts) > 1 else ""
        rec["title"] = (parts[1] if len(parts) > 1 else core).rstrip(".")
        rec["year"] = extract_year(core)
        rec["parse_note"] = "unrecognised style; title is a guess"
    if not rec["year"]:
        rec["year"] = extract_year(ref)
    return rec


# ------------------------------------------------------ list detection ------

def _heading_re(custom):
    if custom:
        body = r"\s+".join(map(re.escape, custom.split()))
        return re.compile(r"^\s*(?:\d+[.)]?\s*)?" + body + r"\s*:?\s*$", re.I)
    return re.compile(r"^\s*(?:\d+[.)]?\s*)?(?:" + DEFAULT_HEADINGS + r")\s*:?\s*$", re.I)


def _gather(lines, start):
    """Collect items after `start`. Returns (items, numbering_mode)."""
    items, cur, prev = [], None, 0
    for text, list_item, pidx in lines[start:]:
        line = re.sub(r"\s+", " ", text).strip()
        if not line or IGNORE_LINE.match(line):
            continue
        if STOP_HEADING.match(line) and not NUM_START.match(line):
            break
        m = NUM_START.match(line)
        typed_ok = m and (cur is None or 0 < _num(m) - prev <= 3)
        if typed_ok or list_item:
            if cur:
                items.append(cur)
            num = _num(m) if typed_ok else prev + 1
            cur = {"num": num, "text": line[m.end():].strip() if typed_ok else line,
                   "para_start": pidx, "para_end": pidx}
            prev = num
        elif cur is not None:
            cur["text"] += ("" if cur["text"].endswith("-") and line[:1].islower() else " ") + line
            cur["para_end"] = pidx if pidx is not None else cur["para_end"]
    if cur:
        items.append(cur)
    return items


def _gather_unnumbered(lines, start):
    """One paragraph = one reference (author year lists in Word / text)."""
    items, n = [], 0
    for text, _, pidx in lines[start:]:
        line = re.sub(r"\s+", " ", text).strip()
        if not line or IGNORE_LINE.match(line):
            continue
        if STOP_HEADING.match(line):
            break
        n += 1
        items.append({"num": n, "text": line, "para_start": pidx, "para_end": pidx})
    return items


def find_reference_list(lines, heading=None):
    """Return (items, heading_line_index or None, warnings)."""
    warnings = []
    hre = _heading_re(heading)
    cands = [i for i, (t, _, _) in enumerate(lines) if hre.match(re.sub(r"\s+", " ", t).strip())]
    best, best_h = [], None
    for h in cands:
        items = _gather(lines, h + 1)
        if len(items) >= len(best):
            best, best_h = items, h
    if cands and not best:
        # Heading found but nothing numbered: one paragraph per reference.
        best_h = cands[-1]
        best = _gather_unnumbered(lines, best_h + 1)
        if best:
            warnings.append("Reference list is not numbered; each paragraph was treated as one reference.")
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


# ------------------------------------------------------------ public API ----

def extract_references(path, heading=None):
    """Returns dict(refs, source_kind, heading_para, warnings, docx_path)."""
    path = Path(path)
    ext = path.suffix.lower()
    warnings, docx_path, heading_para = [], None, None

    if ext in TABLE_EXT:
        refs = refs_from_table(read_table(path))
        return dict(refs=refs, source_kind="table", heading_para=None, warnings=warnings, docx_path=None)

    if ext in (".doc", ".odt") or (ext == ".rtf" and (shutil.which("soffice") or shutil.which("libreoffice"))):
        path = _convert_with_soffice(path)
        ext = ".docx"

    if ext == ".docx":
        lines, docx_path = read_docx_lines(path), path
    elif ext == ".pdf":
        lines = read_pdf_lines(path)
    else:
        lines = read_text_lines(path)

    items, h, w = find_reference_list(lines, heading)
    warnings += w
    if ext == ".docx" and h is not None:
        heading_para = lines[h][2]

    refs = []
    for it in items:
        rec = parse_reference_text(it["text"])
        rec.update(num=it["num"], para_start=it["para_start"], para_end=it["para_end"])
        refs.append(rec)
    kind = "docx" if ext == ".docx" else ext.lstrip(".")
    return dict(refs=refs, source_kind=kind, heading_para=heading_para,
                warnings=warnings, docx_path=docx_path)
