"""Helpers that build Word documents with features python-docx cannot create:
footnotes, endnotes, existing reference manager fields, and a simulated
"after EndNote update" document for round trip tests."""
import copy
import re
import shutil
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def add_endnote_field(paragraph, display, label="3", recnum="3"):
    """Append an existing EndNote citation field showing `display`."""
    xml = (f'<EndNote><Cite><Author>Old</Author><Year>2020</Year><RecNum>{recnum}</RecNum>'
           f'<DisplayText>{display}</DisplayText><record><rec-number>{recnum}</rec-number>'
           f'<label>{label}</label><titles><title>Existing</title></titles></record></Cite></EndNote>')
    for frag in (f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>',
                 f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> ADDIN EN.CITE {_esc(xml)}</w:instrText></w:r>',
                 f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>',
                 f'<w:r {nsdecls("w")}><w:t>{display}</w:t></w:r>',
                 f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>'):
        paragraph._p.append(parse_xml(frag))


def add_mendeley_control(paragraph, display):
    paragraph._p.append(parse_xml(
        f'<w:sdt {nsdecls("w")}><w:sdtPr><w:tag w:val="MENDELEY_CITATION_v3_abc"/></w:sdtPr>'
        f'<w:sdtContent><w:r><w:t>{display}</w:t></w:r></w:sdtContent></w:sdt>'))


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def add_notes(docx_path, footnotes, endnotes):
    """Add footnotes / endnotes (lists of plain text) referenced from the first body paragraph."""
    tmp = Path(str(docx_path) + ".tmp")
    shutil.copy(docx_path, tmp)
    with zipfile.ZipFile(tmp) as zin:
        files = {n: zin.read(n) for n in zin.namelist()}

    def notes_xml(kind, items):
        sep = (f'<w:{kind} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:{kind}>'
               f'<w:{kind} w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:{kind}>')
        body = "".join(f'<w:{kind} w:id="{i}"><w:p><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'
                       f'<w:{kind}Ref/></w:r><w:r><w:t xml:space="preserve"> {t}</w:t></w:r></w:p></w:{kind}>'
                       for i, t in enumerate(items, 1))
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:{kind}s xmlns:w="{W}">'
                f"{sep}{body}</w:{kind}s>").encode()

    rels = files["word/_rels/document.xml.rels"].decode()
    ct = files["[Content_Types].xml"].decode()
    doc = files["word/document.xml"].decode()
    refs = ""
    for kind, items, rid in (("footnote", footnotes, "rIdFn"), ("endnote", endnotes, "rIdEn")):
        if not items:
            continue
        files[f"word/{kind}s.xml"] = notes_xml(kind, items)
        rels = rels.replace("</Relationships>",
                            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                            f'relationships/{kind}s" Target="{kind}s.xml"/></Relationships>')
        ct = ct.replace("</Types>", f'<Override PartName="/word/{kind}s.xml" ContentType="application/'
                        f'vnd.openxmlformats-officedocument.wordprocessingml.{kind}s+xml"/></Types>')
        refs += "".join(f'<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:{kind}Reference w:id="{i}"/></w:r>'
                        for i in range(1, len(items) + 1))
    files["word/_rels/document.xml.rels"] = rels.encode()
    files["[Content_Types].xml"] = ct.encode()
    files["word/document.xml"] = re.sub(r"(<w:body>.*?<w:p\b[^>]*>.*?)(</w:p>)", lambda m: m.group(1) + refs + m.group(2),
                                        doc, count=1, flags=re.S).encode()
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, b in files.items():
            zout.writestr(n, b)
    tmp.unlink()


TEMP = re.compile(r"\{([^{}]*#\d+[^{}]*)\}")


def simulate_endnote_update(src, dst, record_labels, tamper=None, leave_one=False, data_field=False):
    """Replace each {Author, Year #N; ...} temporary citation with an EndNote field.

    record_labels: {record_number: 'label text'} (the RIS Label, i.e. manuscript numbers)
    tamper: {record_number: replacement_record_number} to simulate a wrong mapping
    leave_one: keep the first temporary citation as plain text
    data_field: encode the XML in an EN.CITE.DATA field (base64), as EndNote does when
                abstracts are embedded"""
    import base64
    from refcheck.annotate import _flush_note_parts, _note_parts, _part_element
    doc = Document(str(src))
    first = True
    roots = [doc.element.body] + [_part_element(p) for _, p in _note_parts(doc)]
    work = [t for root in roots for t in root.iter(qn("w:t"))]
    while work:
        t = work.pop(0)
        m = TEMP.search(t.text or "")
        if not m:
            continue
        if leave_one and first:
            first = False
            continue
        first = False
        cites = ""
        for part in m.group(1).split(";"):
            rn = int(part.split("#")[1])
            rn = (tamper or {}).get(rn, rn)
            au, yr = part.split("#")[0].strip().rsplit(",", 1)
            cites += (f"<Cite><Author>{au}</Author><Year>{yr.strip()}</Year><RecNum>{rn}</RecNum>"
                      f"<record><rec-number>{rn}</rec-number><label>{record_labels.get(rn, '')}</label>"
                      f"<titles><title>t</title></titles></record></Cite>")
        xml = f"<EndNote>{cites}</EndNote>"
        r = t.getparent()
        before, after = t.text[: m.start()], t.text[m.end():]
        t.text = before
        new = []
        if data_field == "nested":
            # as EndNote writes a long citation: an EN.CITE field holding a nested EN.CITE.DATA
            # field, the same base64 data (padded with NUL bytes) on both
            b64 = base64.b64encode(xml.encode() + b"\x00").decode()
            new.append(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"><w:fldData xml:space="preserve">{b64}</w:fldData></w:fldChar></w:r>')
            new.append(f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> ADDIN EN.CITE </w:instrText></w:r>')
            new.append(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"><w:fldData xml:space="preserve">{b64}</w:fldData></w:fldChar></w:r>')
            new.append(f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> ADDIN EN.CITE.DATA </w:instrText></w:r>')
            new.append(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>')
        elif data_field:
            b64 = base64.b64encode(xml.encode()).decode()
            new.append(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"><w:fldData xml:space="preserve">{b64}</w:fldData></w:fldChar></w:r>')
            new.append(f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> ADDIN EN.CITE.DATA </w:instrText></w:r>')
        else:
            new.append(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>')
            new.append(f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> ADDIN EN.CITE {_esc(xml)}</w:instrText></w:r>')
        new += [f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>',
                f'<w:r {nsdecls("w")}><w:t>[x]</w:t></w:r>',
                f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>']
        tail = copy.deepcopy(r)
        for tt in tail.iter(qn("w:t")):
            tt.text = after
            work.insert(0, tt)            # the rest of the run may hold more citations
        anchor = r
        for frag in new + [tail]:
            el = parse_xml(frag) if isinstance(frag, str) else frag
            anchor.addnext(el)
            anchor = el
    body = doc.element.body
    body.append(parse_xml(f'<w:p {nsdecls("w")}><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                          f'<w:r><w:instrText> ADDIN EN.REFLIST </w:instrText></w:r>'
                          f'<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'))
    _flush_note_parts(doc)
    doc.save(str(dst))
