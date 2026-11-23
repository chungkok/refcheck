"""XML parsing for content RefCheck did not create (uploaded Word files, NCBI and
GROBID answers). Uses defusedxml when installed; otherwise the standard library
parser. Either way an inline DTD or an entity declaration is refused first, which blocks
entity expansion and external entity attacks; a DOCTYPE naming an external DTD only (as
in every NCBI answer) is accepted, and the external DTD is never fetched."""

from __future__ import annotations

import re
import xml.etree.ElementTree as _ET  # nosec B405 - guarded below

ParseError = _ET.ParseError

try:  # optional dependency
    from defusedxml.ElementTree import fromstring as _defused_fromstring
except ImportError:  # pragma: no cover
    _defused_fromstring = None


class UnsafeXML(ValueError):
    pass


# A DOCTYPE that only names an external DTD is safe and normal: every NCBI E-utilities answer
# starts with one, for example
#   <!DOCTYPE eSearchResult PUBLIC "-//NLM//DTD esearch 20060628//EN" "https://.../esearch.dtd">
# Neither defusedxml nor the standard library parser fetches it. What attacks need is an inline
# DTD (an internal subset in [...]) or an entity declaration, and those are refused.
_INTERNAL_SUBSET = re.compile(r"<!DOCTYPE[^>\[]*\[", re.IGNORECASE)
_DOCTYPE = re.compile(r"<!DOCTYPE\s[^>]*>", re.IGNORECASE)
_WEB_DTD = re.compile(r"""^<!DOCTYPE\s+[\w:.-]+\s+(?:PUBLIC\s+("[^"]*"|'[^']*')\s+|SYSTEM\s+)"""
                      r"""("https?://[^"]*"|'https?://[^']*')\s*>$""", re.IGNORECASE)


def fromstring(text):
    if isinstance(text, bytes):
        head = text[:4096].decode("utf-8", "ignore")
    else:
        head = text[:4096]
    if "<!ENTITY" in head.upper() or _INTERNAL_SUBSET.search(head):
        raise UnsafeXML("XML with an inline DTD or an entity declaration is not accepted")
    for d in _DOCTYPE.findall(head):
        if not _WEB_DTD.match(d.strip()):
            raise UnsafeXML("XML whose DOCTYPE names a local or non web DTD is not accepted")
    if _defused_fromstring is not None:
        return _defused_fromstring(text)
    return _ET.fromstring(text)  # nosec B314 - inline DTDs and entities refused above
