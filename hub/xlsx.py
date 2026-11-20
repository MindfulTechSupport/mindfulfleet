"""A small Excel (.xlsx) writer: one sheet per Pi, a bold heading row, dates Excel understands.

Python has no Excel writer built in, and the hub installs nothing, so this writes the file's XML by hand
inside a zip, row by row, so a big export never has to sit in memory all at once. Google Sheets opens
the same file (File > Import > Upload).
"""
import re
import zipfile

EXCEL_EPOCH_DAYS = 25569  # 1970-01-01 in Excel's day count.
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def column_letters(index):
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def sheet_names(names):
    """Excel's rules: at most 31 characters, none of []:*?/\\, and no two alike (case ignored)."""
    seen, out = set(), []
    for name in names:
        base = _BAD_SHEET.sub("-", _CONTROL.sub("", name)).strip("' ") or "Pi"
        if base.lower() == "history":
            base = "History (Pi)"  # Excel keeps the name History for itself.
        candidate, number = base[:31], 2
        while candidate.lower() in seen:
            suffix = f" ({number})"
            candidate, number = base[:31 - len(suffix)] + suffix, number + 1
        seen.add(candidate.lower())
        out.append(candidate)
    return out


def _text(value):
    text = _CONTROL.sub("", str(value))
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def write(target, heading, sheets, utc_offset):
    """target: a binary file. heading: column names. sheets: [(name, rows)], each row (unix seconds, number...).
    utc_offset(seconds) gives the hub's local offset, so times read as the hub's clock."""
    names = sheet_names([name for name, _ in sheets]) or ["Data"]
    letters = [column_letters(i) for i in range(len(heading))]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", _content_types(len(names)))
        book.writestr("_rels/.rels", _ROOT_RELS)
        book.writestr("xl/workbook.xml", _workbook(names))
        book.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(names)))
        book.writestr("xl/styles.xml", _STYLES)
        for number, (_, rows) in enumerate(sheets or [("Data", [])], start=1):
            with book.open(f"xl/worksheets/sheet{number}.xml", "w", force_zip64=True) as out:
                out.write(_SHEET_START.encode())
                cells = "".join(f'<c r="{letters[i]}1" t="inlineStr" s="2"><is><t>{_text(name)}</t></is></c>'
                                for i, name in enumerate(heading))
                out.write(f'<row r="1">{cells}</row>'.encode())
                for line, row in enumerate(rows, start=2):
                    when = row[0]
                    parts = [f'<c r="A{line}" s="1"><v>{(when + utc_offset(when)) / 86400 + EXCEL_EPOCH_DAYS:.8f}</v></c>']
                    for i, value in enumerate(row[1:], start=1):
                        if value is not None:
                            parts.append(f'<c r="{letters[i]}{line}"><v>{value}</v></c>')
                    out.write(f'<row r="{line}">{"".join(parts)}</row>'.encode())
                out.write(_SHEET_END.encode())


def _content_types(count):
    sheets = "".join(f'<Override PartName="/xl/worksheets/sheet{n}.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     for n in range(1, count + 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f'{sheets}</Types>')


_ROOT_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
              'Target="xl/workbook.xml"/></Relationships>')


def _workbook(names):
    sheets = "".join(f'<sheet name="{_text(name)}" sheetId="{n}" r:id="rId{n}"/>' for n, name in enumerate(names, start=1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{sheets}</sheets></workbook>')


def _workbook_rels(count):
    rels = "".join(f'<Relationship Id="rId{n}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                   f'Target="worksheets/sheet{n}.xml"/>' for n in range(1, count + 1))
    rels += (f'<Relationship Id="rId{count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
             'Target="styles.xml"/>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')


_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts>'
           '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
           '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
           '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
           '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
           '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
           '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
           '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
           '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
           '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
           '</styleSheet>')

_SHEET_START = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                '</sheetView></sheetViews><cols><col min="1" max="1" width="20" customWidth="1"/>'
                '<col min="2" max="60" width="14" customWidth="1"/></cols><sheetData>')
_SHEET_END = "</sheetData></worksheet>"
