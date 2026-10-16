"""Writes Mara's sheet, an ODS file to fill in: sheet.py OUT.ods

One row per office with the months as columns, a Q4 total column and an "All offices" row, and a summary below.
The figures and the cells for formulas are empty. The file is the same on every build: fixed dates in the
metadata and in the ZIP entries.
"""

import sys
import zipfile
from xml.sax.saxutils import escape

from figures import AVERAGE_LABEL, FIRST_ROW, GROWTH_LABEL, MONTHS, NOTE, OFFICES, SHEET_NAME, TITLE, TOTAL_ROW

NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2" '
    'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" office:version="1.3"'
)

STYLES = """<office:automatic-styles>
<style:style style:name="co1" style:family="table-column"><style:table-column-properties style:column-width="7.5cm"/></style:style>
<style:style style:name="co2" style:family="table-column"><style:table-column-properties style:column-width="3.2cm"/></style:style>
<number:number-style style:name="N1"><number:number number:decimal-places="0" number:min-decimal-places="0" number:min-integer-digits="1" number:grouping="true"/></number:number-style>
<number:percentage-style style:name="N2"><number:number number:decimal-places="1" number:min-decimal-places="1" number:min-integer-digits="1"/><number:text>%</number:text></number:percentage-style>
<style:style style:name="title" style:family="table-cell" style:parent-style-name="Default"><style:text-properties fo:font-size="15pt" fo:font-weight="bold"/></style:style>
<style:style style:name="note" style:family="table-cell" style:parent-style-name="Default"><style:text-properties fo:color="#555555"/></style:style>
<style:style style:name="head" style:family="table-cell" style:parent-style-name="Default"><style:table-cell-properties fo:background-color="#dde4ee" fo:border-bottom="0.75pt solid #000000"/><style:text-properties fo:font-weight="bold"/></style:style>
<style:style style:name="headnum" style:family="table-cell" style:parent-style-name="Default"><style:table-cell-properties fo:background-color="#dde4ee" fo:border-bottom="0.75pt solid #000000"/><style:paragraph-properties fo:text-align="end"/><style:text-properties fo:font-weight="bold"/></style:style>
<style:style style:name="num" style:family="table-cell" style:parent-style-name="Default" style:data-style-name="N1"/>
<style:style style:name="total" style:family="table-cell" style:parent-style-name="Default"><style:table-cell-properties fo:border-top="0.75pt solid #000000"/><style:text-properties fo:font-weight="bold"/></style:style>
<style:style style:name="totalnum" style:family="table-cell" style:parent-style-name="Default" style:data-style-name="N1"><style:table-cell-properties fo:border-top="0.75pt solid #000000"/><style:text-properties fo:font-weight="bold"/></style:style>
<style:style style:name="bold" style:family="table-cell" style:parent-style-name="Default"><style:text-properties fo:font-weight="bold"/></style:style>
<style:style style:name="pct" style:family="table-cell" style:parent-style-name="Default" style:data-style-name="N2"/>
</office:automatic-styles>"""


def text_cell(text, style=None):
    attrs = f' table:style-name="{style}"' if style else ""
    return f'<table:table-cell{attrs} office:value-type="string"><text:p>{escape(text)}</text:p></table:table-cell>'


def empty_cell(style=None, repeat=1):
    attrs = f' table:style-name="{style}"' if style else ""
    if repeat > 1:
        attrs += f' table:number-columns-repeated="{repeat}"'
    return f"<table:table-cell{attrs}/>"


def row(*cells):
    return "<table:table-row>" + "".join(cells) + "</table:table-row>"


def content():
    rows = [
        row(text_cell(TITLE, "title"), empty_cell(repeat=4)),
        row(text_cell(NOTE, "note"), empty_cell(repeat=4)),
        row(empty_cell(repeat=5)),
        row(text_cell("Office", "head"), *(text_cell(m, "headnum") for m in MONTHS), text_cell("Q4 total", "headnum")),
    ]
    assert len(rows) + 1 == FIRST_ROW
    rows += [row(text_cell(office), empty_cell("num", 4)) for office in OFFICES]
    assert len(rows) + 1 == TOTAL_ROW
    rows += [
        row(text_cell("All offices", "total"), empty_cell("totalnum", 4)),
        row(empty_cell(repeat=5)),
        row(text_cell("Summary", "bold"), empty_cell(repeat=4)),
        row(text_cell(AVERAGE_LABEL), empty_cell("num"), empty_cell(repeat=3)),
        row(text_cell(GROWTH_LABEL), empty_cell("pct"), empty_cell(repeat=3)),
    ]
    table = (
        f'<table:table table:name="{escape(SHEET_NAME)}">'
        '<table:table-column table:style-name="co1"/>'
        '<table:table-column table:style-name="co2" table:number-columns-repeated="4"/>'
        + "".join(rows) + "</table:table>"
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content {NS}>{FONTS}{STYLES}'
        f"<office:body><office:spreadsheet>{table}</office:spreadsheet></office:body></office:document-content>"
    )


META = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta {NS}><office:meta>
<meta:initial-creator>Mara Lindqvist</meta:initial-creator><dc:creator>Mara Lindqvist</dc:creator>
<meta:creation-date>2026-01-08T10:12:00</meta:creation-date><dc:date>2026-03-09T16:40:00</dc:date>
<dc:title>{escape(TITLE)}</dc:title>
</office:meta></office:document-meta>"""

FONTS = """<office:font-face-decls><style:font-face style:name="Liberation Sans" svg:font-family="&apos;Liberation Sans&apos;"
style:font-family-generic="swiss" style:font-pitch="variable"/></office:font-face-decls>"""

STYLES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles {NS}>{FONTS}<office:styles>
<style:default-style style:family="table-cell"><style:text-properties style:font-name="Liberation Sans" fo:font-size="10pt" fo:language="en" fo:country="US"/></style:default-style>
<style:style style:name="Default" style:family="table-cell"/>
</office:styles></office:document-styles>"""

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">
<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>
<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>
</manifest:manifest>"""


def main(out):
    with zipfile.ZipFile(out, "w") as z:
        def add(name, data, compress=zipfile.ZIP_DEFLATED):
            info = zipfile.ZipInfo(name, date_time=(2026, 3, 9, 16, 40, 0))
            info.compress_type = compress
            z.writestr(info, data)

        # The mimetype comes first and uncompressed, as ODF requires.
        add("mimetype", "application/vnd.oasis.opendocument.spreadsheet", zipfile.ZIP_STORED)
        add("content.xml", content())
        add("styles.xml", STYLES_XML)
        add("meta.xml", META)
        add("META-INF/manifest.xml", MANIFEST)


if __name__ == "__main__":
    main(sys.argv[1])
