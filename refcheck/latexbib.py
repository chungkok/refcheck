"""Reference input from LaTeX projects: BibTeX (.bib), compiled bibliographies (.bbl)
and the thebibliography environment of a .tex file.

No third party parser is needed. The BibTeX reader handles braces, quotes, numbers,
string concatenation with #, @string macros, and the usual LaTeX accents. It is a
reader for reference checking, not a full BibTeX implementation.
"""

from __future__ import annotations

import re
from typing import Dict, List

_ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303", "c": "\u0327",
            "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b", "k": "\u0328"}
_SPECIAL = {r"\ss": "ß", r"\o": "ø", r"\O": "Ø", r"\ae": "æ", r"\AE": "Æ", r"\aa": "å", r"\AA": "Å",
            r"\l": "ł", r"\L": "Ł", r"\i": "ı", r"\&": "&", r"\%": "%", r"\_": "_", r"\$": "$", r"\#": "#"}
_MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}


def latex_to_text(s: str) -> str:
    """Plain Unicode text from a LaTeX fragment (accents, simple commands, braces)."""
    import unicodedata
    s = str(s or "")
    s = re.sub(r"(?<!\\)%[^\n]*", "", s)                   # comments
    s = re.sub(r"\\url\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\href\{([^}]*)\}\{([^}]*)\}", r"\2 \1", s)

    def acc(m):
        mark, ch = m.group(1), m.group(2) or m.group(3)
        if ch in ("\\i", "i") and mark != "c":
            ch = "i"
        return unicodedata.normalize("NFC", ch + _ACCENTS[mark])
    s = re.sub(r"\\([`'^\"~=.])\s*(?:\{\s*(\\?[A-Za-z])\s*\}|(\\?[A-Za-z]))", acc, s)
    s = re.sub(r"\\([cuvHk])\s*(?:\{\s*(\\?[A-Za-z])\s*\}|\s+([A-Za-z]))", acc, s)
    for k, v in sorted(_SPECIAL.items(), key=lambda kv: -len(kv[0])):
        s = re.sub(re.escape(k) + r"(?![A-Za-z])\s?", v, s)
    s = re.sub(r"\\(?:emph|textit|textbf|textsc|textrm|textup|mbox|text|bibinfo\{[^}]*\}|newblock|bibfield\{[^}]*\})\s*", "", s)
    s = re.sub(r"\\(?:em|it|bf|sc|rm|newblock|natexlab\{[^}]*\})\b\s*", "", s)
    s = re.sub(r"\\[A-Za-z]+\*?", "", s)                    # other commands: drop the name
    s = s.replace("~", " ").replace("---", "\u2014").replace("--", "\u2013")
    s = s.replace("``", '"').replace("''", '"')
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- BibTeX ----

def _read_value(src: str, i: int, strings: Dict[str, str]):
    """Value starting at src[i]; returns (raw value, next index)."""
    parts = []
    n = len(src)
    while True:
        while i < n and src[i].isspace():
            i += 1
        if i >= n:
            break
        c = src[i]
        if c == "{":
            depth, j = 0, i
            while j < n:
                if src[j] == "{" and src[j - 1:j] != "\\":
                    depth += 1
                elif src[j] == "}" and src[j - 1:j] != "\\":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            parts.append(src[i + 1:j])
            i = j + 1
        elif c == '"':
            j, depth = i + 1, 0
            while j < n and not (src[j] == '"' and depth == 0 and src[j - 1] != "\\"):
                depth += src[j] == "{"
                depth -= src[j] == "}"
                j += 1
            parts.append(src[i + 1:j])
            i = j + 1
        else:
            m = re.match(r"[^,#}\s]+", src[i:])
            tok = m.group(0) if m else ""
            parts.append(strings.get(tok.lower(), tok))
            i += len(tok) or 1
        while i < n and src[i].isspace():
            i += 1
        if i < n and src[i] == "#":
            i += 1
            continue
        break
    return "".join(parts), i


def parse_bibtex(src: str) -> List[Dict[str, str]]:
    """Entries as dicts: ENTRYTYPE, ID and lower case field names (values still LaTeX)."""
    entries, strings = [], {}
    for m in re.finditer(r"@\s*([A-Za-z]+)\s*[{(]", src):
        typ = m.group(1).lower()
        i = m.end()
        if typ in ("comment", "preamble"):
            continue
        if typ == "string":
            k = re.match(r"\s*([^\s=]+)\s*=", src[i:])
            if k:
                v, _ = _read_value(src, i + k.end(), strings)
                strings[k.group(1).lower()] = v
            continue
        km = re.match(r"\s*([^,\s]*)\s*,", src[i:])
        if not km:
            continue
        e = {"ENTRYTYPE": typ, "ID": km.group(1)}
        i += km.end()
        while True:
            fm = re.match(r"\s*([A-Za-z][\w\-:.]*)\s*=\s*", src[i:])
            if not fm:
                break
            v, i = _read_value(src, i + fm.end(), strings)
            e[fm.group(1).lower()] = v
            j = re.match(r"\s*,?", src[i:])
            i += j.end()
        entries.append(e)
    return entries


def _split_bib_names(names: str) -> List[str]:
    """'Smith, John A. and Jane Doe and others' -> ['Smith JA', 'Doe J', 'et al']."""
    out = []
    depth, cur, parts = 0, "", []
    for tok in re.split(r"(\{|\}|\s+and\s+)", names):
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
        if depth == 0 and re.fullmatch(r"\s+and\s+", tok or ""):
            parts.append(cur)
            cur = ""
        else:
            cur += tok or ""
    parts.append(cur)
    for p in parts:
        raw = p.strip()
        if not raw:
            continue
        if raw.lower() == "others":
            out.append("et al")
            continue
        if raw.startswith("{") and raw.endswith("}") and raw.count("{") == 1:
            out.append(latex_to_text(raw))           # corporate author
            continue
        p = latex_to_text(raw)
        if "," in p:
            bits = [b.strip() for b in p.split(",")]
            last, first = bits[0], bits[-1] if len(bits) > 1 else ""
            if len(bits) == 3:                       # von Last, Jr, First
                last, first = f"{bits[0]} {bits[1]}", bits[2]
        else:
            ws = p.split()
            # lower case particles stay with the surname (van, de, von)
            k = len(ws) - 1
            while k > 0 and ws[k - 1][:1].islower():
                k -= 1
            last, first = " ".join(ws[k:]), " ".join(ws[:k])
        initials = "".join(w[0].upper() for w in re.split(r"[\s.\-]+", first) if w and w[0].isalpha())
        out.append(f"{last} {initials}".strip())
    return out


def _vancouver_line(rec: Dict[str, str]) -> str:
    part = rec["year"]
    if rec["volume"]:
        part += f";{rec['volume']}" + (f"({rec['issue']})" if rec["issue"] else "")
    if rec["pages"]:
        part += f":{rec['pages']}"
    s = ". ".join(x for x in (rec["authors"], rec["title"], rec["journal"], part) if x) + "."
    return s + (f" doi:{rec['doi']}" if rec["doi"] else "")


def refs_from_bibtex(src: str, blank) -> List[Dict]:
    """References from BibTeX source. `blank` is extract._blank (avoids a cycle)."""
    refs = []
    for n, e in enumerate(parse_bibtex(src), 1):
        rec = blank("")
        names = _split_bib_names(e.get("author") or e.get("editor") or "")
        rec["authors"] = ", ".join(names)
        rec["title"] = latex_to_text(e.get("title", "")).rstrip(".")
        rec["journal"] = latex_to_text(e.get("journal") or e.get("journaltitle") or e.get("booktitle")
                                       or e.get("publisher") or "")
        y = re.search(r"(1[89]|20)\d\d", e.get("year") or e.get("date") or "")
        rec["year"] = y.group(0) if y else ""
        rec["volume"] = latex_to_text(e.get("volume", ""))
        rec["issue"] = latex_to_text(e.get("number") or e.get("issue") or "")
        rec["pages"] = latex_to_text(e.get("pages", "")).replace("\u2013", "-").replace(" ", "")
        doi = latex_to_text(e.get("doi", ""))
        rec["doi"] = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi, flags=re.I)
        pm = re.sub(r"\D", "", e.get("pmid", ""))
        rec["pmid_hint"] = pm
        rec["text"] = _vancouver_line(rec)
        rec["num"] = n
        rec["bibtex_key"] = e["ID"]
        rec["parse_method"] = "bibtex"
        if not rec["title"]:
            rec["parse_note"] = f"BibTeX entry {e['ID']} has no title"
        refs.append(rec)
    return refs


# ------------------------------------------------------- thebibliography ----

def bibitems(src: str) -> List[Dict[str, str]]:
    """[{key, text}] from \\bibitem entries of a .bbl or a thebibliography environment."""
    m = re.search(r"\\begin\{thebibliography\}(?:\{[^}]*\})?(.*?)(?:\\end\{thebibliography\}|$)", src, re.S)
    body = m.group(1) if m else src
    out = []
    pieces = re.split(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", body)
    for k in range(1, len(pieces) - 1, 2):
        out.append(dict(key=pieces[k].strip(), text=latex_to_text(pieces[k + 1])))
    return out


def has_thebibliography(src: str) -> bool:
    return bool(re.search(r"\\bibitem\b", src))


def bib_files_named(src: str) -> List[str]:
    names = []
    for m in re.finditer(r"\\(?:bibliography|addbibresource)\{([^}]*)\}", src):
        names += [x.strip() for x in m.group(1).split(",") if x.strip()]
    return names
