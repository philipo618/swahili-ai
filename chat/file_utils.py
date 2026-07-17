"""Extract text from office documents without extra dependencies."""
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


def _xml_text(element) -> str:
    parts = []
    if element.text:
        parts.append(element.text)
    for child in element:
        parts.append(_xml_text(child))
        if child.tail:
            parts.append(child.tail)
    return ''.join(parts)


def extract_docx_text(path: Path, max_chars: int = 12000) -> str:
    """Read plain text from a .docx file."""
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open('word/document.xml') as doc:
                tree = ET.parse(doc)
                root = tree.getroot()
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                paragraphs = []
                for para in root.findall('.//w:p', ns):
                    text = _xml_text(para).strip()
                    if text:
                        paragraphs.append(text)
                return '\n'.join(paragraphs)[:max_chars]
    except Exception:
        return ''


def extract_xlsx_text(path: Path, max_chars: int = 12000) -> str:
    """Read cell text from a .xlsx file (shared strings + inline)."""
    try:
        with zipfile.ZipFile(path) as zf:
            strings = []
            if 'xl/sharedStrings.xml' in zf.namelist():
                with zf.open('xl/sharedStrings.xml') as ss:
                    tree = ET.parse(ss)
                    root = tree.getroot()
                    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                    for si in root.findall('.//m:si', ns):
                        strings.append(_xml_text(si))

            rows = []
            sheet_files = sorted(n for n in zf.namelist() if n.startswith('xl/worksheets/sheet'))
            for sheet_name in sheet_files[:3]:
                with zf.open(sheet_name) as sheet:
                    tree = ET.parse(sheet)
                    root = tree.getroot()
                    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                    for row in root.findall('.//m:row', ns):
                        cells = []
                        for cell in row.findall('m:c', ns):
                            ref = cell.get('t')
                            val_el = cell.find('m:v', ns)
                            if val_el is None or val_el.text is None:
                                inline = cell.find('m:is', ns)
                                if inline is not None:
                                    cells.append(_xml_text(inline))
                                continue
                            if ref == 's':
                                idx = int(val_el.text)
                                if idx < len(strings):
                                    cells.append(strings[idx])
                            else:
                                cells.append(val_el.text)
                        if cells:
                            rows.append('\t'.join(cells))

            return '\n'.join(rows)[:max_chars]
    except Exception:
        return ''


def extract_office_text(path: Path, filename: str, max_chars: int = 12000) -> str:
    lower = filename.lower()
    if lower.endswith('.docx'):
        return extract_docx_text(path, max_chars)
    if lower.endswith('.xlsx') or lower.endswith('.xls'):
        if lower.endswith('.xlsx'):
            return extract_xlsx_text(path, max_chars)
    return ''
