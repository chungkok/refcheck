"""Small text helpers shared by the extractor and the matcher."""

import re
import unicodedata
from difflib import SequenceMatcher

DASHES = "‐‑‒–—−"
DASH_RE = "[-" + DASHES + "]"

_SPELLING = {
    "tumours": "tumors", "tumour": "tumor", "paediatric": "pediatric",
    "paediatrics": "pediatrics", "centre": "center", "centres": "centers",
    "behaviour": "behavior", "organisation": "organization",
    "organisations": "organizations", "haematological": "hematological",
    "haematology": "hematology", "haematopoietic": "hematopoietic",
    "leukaemia": "leukemia", "leukaemias": "leukemias", "oesophageal": "esophageal",
    "anaemia": "anemia", "characterisation": "characterization",
    "randomised": "randomized", "optimised": "optimized",
}


def norm_dashes(s):
    for d in DASHES:
        s = s.replace(d, "-")
    return s


# Letters that Unicode does not decompose into a base letter and an accent, written as they are
# usually transliterated (Óskarsson JÞ, Ðorđević, Øvrebø, Łukasz, Æbelø, Straße). Without this
# they were dropped, and "JÞ" stopped being initials.
_TRANSLIT = str.maketrans({"Þ": "Th", "þ": "th", "Ð": "D", "ð": "d", "Đ": "D", "đ": "d", "Ł": "L", "ł": "l",
                           "Ø": "O", "ø": "o", "Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe", "ß": "ss", "ı": "i",
                           "Ħ": "H", "ħ": "h", "Ŧ": "T", "ŧ": "t", "Ŋ": "N", "ŋ": "n", "ĸ": "k"})


def fold_letters(s):
    """Accented and special Latin letters to plain letters, case kept: 'Óskarsson JÞ' ->
    'Oskarsson JTh', 'Ðorđević' -> 'Dordevic'."""
    s = unicodedata.normalize("NFKD", str(s).translate(_TRANSLIT))
    return "".join(c for c in s if not unicodedata.combining(c))


def clean_text(s):
    """Lower case ASCII words separated by single spaces."""
    if s is None:
        return ""
    s = fold_letters(s)
    s = norm_dashes(s).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_normalize(s):
    return " ".join(_SPELLING.get(w, w) for w in clean_text(s).split())


def compact(s):
    return re.sub(r"[^a-z0-9]", "", clean_text(s))


def similarity(a, b):
    a, b = title_normalize(a), title_normalize(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# Two words count as spelling variants of each other (leukaemia, leukemia) at this similarity.
# Varied in the sensitivity analysis of the validation.
VARIANT_RATIO = 0.75
# The whole swapped word and number rule; False only to measure what it contributes.
SWAP_CHECK = True

_STOP = frozenset("a an and as at by for from in into of on or the to with versus vs".split())


def content_swapped(a, b):
    """True when two similar titles differ by a swapped content word or number, for example
    "Acute myeloid leukemia: 2023 update ..." against "Multiple myeloma: 2024 update ...".
    A word dropped on one side, or a spelling variant (leukaemia, leukemia), is not a swap."""
    if not SWAP_CHECK:
        return False

    def digits(t):
        return {w for w in re.findall(r"[a-z0-9]+", t) if any(c.isdigit() for c in w)}
    ta, tb = title_normalize(a), title_normalize(b)
    if digits(ta) and digits(tb) and digits(ta) != digits(tb):
        return True
    wa = {w for w in ta.split() if w not in _STOP}
    wb = {w for w in tb.split() if w not in _STOP}
    only_a, only_b = wa - wb, wb - wa
    if not only_a or not only_b:
        return False
    lone_a = [x for x in only_a if not any(SequenceMatcher(None, x, y).ratio() >= VARIANT_RATIO for y in only_b)]
    lone_b = [y for y in only_b if not any(SequenceMatcher(None, y, x).ratio() >= VARIANT_RATIO for x in only_a)]
    return bool(lone_a and lone_b)


def token_overlap(a, b):
    aa, bb = set(title_normalize(a).split()), set(title_normalize(b).split())
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def extract_year(s):
    m = re.search(r"\b(?:19|20)\d{2}\b", str(s or ""))
    return m.group(0) if m else ""


_INITIALS_RE = re.compile(r"(?:[A-Z]\.?(?:-[A-Za-z]\.?)?){1,4}|Jr\.?|Sr\.?|II|III|IV")


def _fold_accents(token):
    # Þ, Ð, Ø, Ł ... as one capital each, so 'JÞ' and 'KØ' still read as initials
    return fold_letters(token.translate(_INITIAL_LETTER))


_INITIAL_LETTER = str.maketrans({"Þ": "T", "Ð": "D", "Đ": "D", "Ł": "L", "Ø": "O", "Æ": "A", "Œ": "O", "Ħ": "H",
                                 "Ŧ": "T", "Ŋ": "N"})


def is_initials(token):
    """'DJ', 'J.R.', 'S-l', 'C-I', 'T-H', 'Jr' (an initials or suffix token after a surname).
    Accented capitals count: PubMed writes 'Diaz Arias JÁ', 'Perez MÁ', 'Perez-Benito Á'."""
    return bool(_INITIALS_RE.fullmatch(_fold_accents(token)))


def split_author(a):
    """'Van de Donk NWCJ' -> ('van de donk', 'NWCJ'); 'Lim S-l' -> ('lim', 'SL');
    initials first (Science, IEEE, Chicago after the first author): 'A. V. Finck' -> ('finck', 'AV')."""
    parts = a.strip().strip(".").split()
    lead = []
    while len(parts) > 1 and re.fullmatch(r"(?:[A-Z\u00C0-\u00DE]\.-?)+", parts[0]):
        lead.append(parts.pop(0))
    if lead and not is_initials(parts[-1]):
        return clean_text(" ".join(parts)), re.sub(r"[^A-Za-z]", "", _fold_accents("".join(lead))).upper()
    parts = lead + parts
    ini = []
    while len(parts) > 1 and is_initials(parts[-1]):
        tok = parts.pop()
        if not re.fullmatch(r"Jr\.?|Sr\.?|II|III|IV", tok):
            ini.insert(0, re.sub(r"[^A-Za-z]", "", _fold_accents(tok)).upper())
    return clean_text(" ".join(parts)), "".join(ini)


def author_list(authors):
    """Listed authors in order, without 'et al'."""
    out = []
    authors = re.sub(r"\s+(?:and|&)\s+", ", ", authors or "")     # 'Brunet,M. and Martin,S.J.'
    for a in re.split(r"[;,]", authors):
        a = re.sub(r"\bet\.? al\.?$", "", a.strip(), flags=re.I).strip(" .")
        a = re.sub(r"^(?:and|&)\s+", "", a).strip()          # '..., & Finck, A. V.'  '..., and Ding L'
        if a.lower() in ("and", "&"):
            continue
        if not a:
            continue
        if out and re.fullmatch(r"(Jr|Sr|II|III|IV)\.?", a):
            out[-1] = f"{out[-1]} {a}"          # 'Shaughnessy JD, Jr'
        elif (out and is_initials(a.replace(" ", "")) and not is_initials(out[-1].split()[-1])
              and not re.match(r"[A-Z\u00C0-\u00DE]\.", out[-1])):
            out[-1] = f"{out[-1]} {a}"          # 'Kok, CH' and 'San Miguel, J.' written surname first
        else:
            out.append(a)
    return out


def first_author_surname(authors):
    """'Patel A, Dogan H' -> 'patel';  'von Deimling A' -> 'von deimling'."""
    if not authors:
        return ""
    first = re.split(r";|,|\bet al\b|\band\b|&", authors, flags=re.I)[0].strip()
    first = re.sub(r"^\d+[.)]\s*", "", first)
    parts = first.split()
    lead = 0
    while lead < len(parts) - 1 and re.fullmatch(r"(?:[A-Z\u00C0-\u00DE]\.-?)+", parts[lead]):
        lead += 1
    if lead and not is_initials(parts[-1]):
        return clean_text(" ".join(parts[lead:]))          # 'T. J. Ley', 'A. Berjis'
    # Drop trailing initials ("Patel A", "Brat DJ", "Smith J.R.")
    while len(parts) > 1 and is_initials(parts[-1]):
        parts.pop()
    return clean_text(" ".join(parts))


def display_surname(authors):
    """Like first_author_surname but keeps original capitalisation."""
    if not authors:
        return ""
    first = re.split(r";|,|\bet al\b|\band\b|&", authors, flags=re.I)[0].strip()
    parts = first.split()
    while len(parts) > 1 and is_initials(parts[-1]):
        parts.pop()
    return " ".join(parts).strip(" .")


def first_page(p):
    """'609-612' -> '609', 'iv1-iv66' -> 'iv1', 'e123' -> 'e123'."""
    if not p:
        return ""
    first = re.split(r"\s*" + DASH_RE + r"\s*", str(p).strip())[0]
    return clean_text(first).replace(" ", "")


def expand_pages(p):
    """MEDLINE abbreviates page ranges: '1283-94' -> ('1283', '1294')."""
    p = norm_dashes(str(p or "")).strip()
    if "-" not in p:
        return p, ""
    sp, ep = [x.strip() for x in p.split("-", 1)]
    if sp.isdigit() and ep.isdigit() and len(ep) < len(sp):
        ep = sp[: len(sp) - len(ep)] + ep
    else:
        m1, m2 = re.match(r"([A-Za-z]*)(\d+)$", sp), re.match(r"([A-Za-z]*)(\d+)$", ep)
        if m1 and m2 and not m2.group(1) and len(m2.group(2)) < len(m1.group(2)):
            d = m1.group(2)
            ep = m1.group(1) + d[: len(d) - len(m2.group(2))] + m2.group(2)
    return sp, ep


_TITLE_IN_TEXT_CACHE: dict = {}


def title_in_text(title, text):
    """Similarity of a record's title to its best matching stretch of a reference's raw text
    (0 to 1), whatever the citation style: quotes, title case, position and punctuation do
    not matter. Used so a misread reference can still be compared with the right paper."""
    if not title or not text:
        return 0.0
    key = (title, text)
    if key in _TITLE_IN_TEXT_CACHE:
        return _TITLE_IN_TEXT_CACHE[key]
    tw, xw = clean_text(title).split(), clean_text(text).split()
    best = 0.0
    if len(tw) >= 3 and xw:
        tset = set(tw)
        if sum(1 for w in tset if w in set(xw)) / len(tset) >= 0.7:     # cheap filter first
            t = " ".join(tw)
            if t in " ".join(xw):
                best = 1.0
            else:
                n = len(tw)
                for size in (n - 1, n, n + 1):
                    if size < 1:
                        continue
                    for i in range(0, max(1, len(xw) - size + 1)):
                        best = max(best, similarity(t, " ".join(xw[i:i + size])))
                        if best >= 0.995:
                            break
    if len(_TITLE_IN_TEXT_CACHE) > 50_000:
        _TITLE_IN_TEXT_CACHE.clear()
    _TITLE_IN_TEXT_CACHE[key] = best
    return best


def surname_at_start(surname, text):
    """True when the record's first author surname is the first name of the raw reference,
    written either way round ('Ley TJ', 'Ley, T. J.', 'T. J. Ley'): leading initials and a list
    number are skipped. A surname that only appears later (a second author) does not count."""
    s = clean_text(surname).split()
    words = clean_text(re.sub(r"^\s*\[?\d+\]?[.)]?\s*", "", text or "")[:160]).split()
    while words and len(words[0]) == 1:
        words.pop(0)
    return bool(s) and words[:len(s)] == s
