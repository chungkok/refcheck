"""Side by side comparison of a parsed reference and a PubMed record."""

from .textutil import (author_list, clean_text, compact, first_author_surname, first_page, norm_dashes,
                       similarity, split_author)


def _same(ok):
    return "✅" if ok is True else ("❌" if ok is False else "–")


def compare_fields(ref, cand):
    """Rows of (field, your document, PubMed, verdict) for the review screen."""
    rows = []
    ts = similarity(ref["title"], cand["title"])
    rows.append(("Title", ref["title"], cand["title"],
                 f"{_same(ts >= 0.94 if ref['title'] else None)} {round(100 * ts)}%"))

    ra, ca = first_author_surname(ref["authors"]), clean_text(cand.get("first_author", ""))
    rows.append(("First author", ref["authors"].split(",")[0] if ref["authors"] else "",
                 cand.get("first_author", ""),
                 _same((ra.replace(" ", "") == ca.replace(" ", "")) if ra and ca else None)))

    rj = compact(ref["journal"])
    cjs = [compact(cand.get("journal", "")), compact(cand.get("iso_journal", ""))]
    rows.append(("Journal", ref["journal"], cand.get("iso_journal") or cand.get("journal", ""),
                 _same((rj in cjs or any(c and (rj in c or c in rj) for c in cjs)) if rj else None)))

    rows.append(("Authors", ref.get("authors", ""), cand.get("authors", ""), _authors_verdict(ref, cand)))

    ry, cy, ey = str(ref.get("year") or ""), str(cand.get("year") or ""), str(cand.get("epub_year") or "")
    if ry and cy and ry != cy and ry == ey:
        yv = f"✅ online year (print {cy})"     # citing the electronic publication year is accepted
    else:
        yv = _same(ry == cy if ry and cy else None)
    rows.append(("Year", ry, cy + (f" (online {ey})" if ey and ey != cy else ""), yv))

    for label, k in (("Volume", "volume"), ("Issue", "issue")):
        a, b = str(ref.get(k, "") or ""), str(cand.get(k, "") or "")
        rows.append((label, a, b, _same(clean_text(a) == clean_text(b) if a and b else None)))

    rp, cp = first_page(ref["pages"]), first_page(cand.get("pages", ""))
    rows.append(("Pages", norm_dashes(ref["pages"] or ""), cand.get("pages", ""),
                 _same(rp == cp if rp and cp else None)))

    rd, cd = (ref.get("doi") or "").lower().strip("."), (cand.get("doi") or "").lower()
    rows.append(("DOI", ref.get("doi", ""), cand.get("doi", ""), _same(rd == cd if rd and cd else None)))
    return rows


def _authors_verdict(ref, cand):
    """✅ when every listed author is in the PubMed author list, else ❌ naming the ones that are not."""
    from .verify import _indexed_compound
    names = author_list(ref.get("authors", ""))
    record = [split_author(a) for a in (cand.get("authors") or "").split(";") if a.strip()]
    if not names or not record:
        return _same(None)
    listed = _indexed_compound([split_author(n) for n in names], record)
    have = {s.replace(" ", "") for s, _ in record}
    missing = [n for n, (s, i) in zip(names, listed) if i and s.replace(" ", "") not in have]
    return "❌ not in PubMed: " + ", ".join(missing) if missing else _same(True)
