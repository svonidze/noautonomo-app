"""Deterministic native review workbook, deliberately outside the AEAT contract."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import os
from typing import Any, Mapping
import xml.etree.ElementTree as ET
import zipfile


REGISTER_COLUMNS = (
    ('tax_date', 'Tax date'), ('kind', 'Kind'), ('component', 'Component'),
    ('taxable_base_eur', 'Tax base (EUR)'), ('deductible_irpf_eur', 'IRPF expense (EUR)'),
    ('vat_eur', 'VAT (EUR)'), ('deductible_vat_eur', 'Deductible VAT (EUR)'),
    ('withholding_eur', 'Withholding (EUR)'), ('amount_eur', 'Amount (EUR)'),
    ('document_number', 'Document number'), ('issued_on', 'Issue date'),
    ('receipt_number', 'Receipt registration number'), ('registered_on', 'Registration date'),
    ('receipt_series', 'Receipt series'), ('receipt_year', 'Receipt year'),
    ('operation_date', 'Operation date'), ('document_type', 'Document type'),
    ('correction_of_transaction_id', 'Corrected transaction ID'), ('correction_kind', 'Correction kind'),
    ('vat_rate_percent', 'VAT rate (%)'), ('invoice_type', 'Invoice type'),
    ('operation_key', 'Operation key'), ('operation_qualification', 'Operation qualification'),
    ('exempt_operation', 'Exemption'), ('reverse_charge', 'Reverse charge'),
    ('include_modelo130', 'Included in Modelo 130'), ('include_modelo303', 'Included in Modelo 303'),
    ('counterparty_name', 'Counterparty'), ('transaction_id', 'Row ID'),
    ('counterparty_tax_id', 'Counterparty tax ID'), ('vat_id', 'Counterparty VAT ID'),
    ('counterparty_identifier', 'Confirmed identifier'), ('counterparty_id_type', 'Identifier type'),
    ('counterparty_identity_country', 'Identifier country'), ('amortization_entry_id', 'Depreciation entry'),
    ('country_code', 'Counterparty country'),
    ('source_transaction_id', 'Source transaction ID'), ('attribution_id', 'Attribution ID'),
    ('document_id', 'Document ID'), ('document_source_hash', 'Document SHA-256'),
    ('attribution_evidence_ref', 'Attribution evidence'), ('fx_evidence_ref', 'FX evidence'),
    ('tax_code', 'Tax code'), ('vat_investment_good', 'VAT investment good'),
)
SOURCE_COLUMNS = (
    ('transaction_id', 'Source transaction ID'), ('tax_date', 'Source date'),
    ('amount_eur', 'Source amount (EUR)'), ('taxable_base_eur', 'Source tax base (EUR)'),
    ('vat_eur', 'Source VAT (EUR)'), ('document_number', 'Document number'),
    ('issued_on', 'Issue date'), ('counterparty_name', 'Counterparty'),
    ('counterparty_tax_id', 'Counterparty tax ID'), ('vat_id', 'Counterparty VAT ID'),
    ('document_id', 'Document ID'), ('document_source_hash', 'Document SHA-256'),
)
MAIN_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
DOC_REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REVIEW_XLSX_FILENAME = 'tax-registers-review.xlsx'


def write_tax_register_review_xlsx(path: str | Path, projection: Mapping[str, Any]) -> Path:
    """Write the same effective registers as CSV/JSON without implying import support."""
    target = Path(path)
    if target.suffix.lower() != '.xlsx':
        raise ValueError('Tax register review output must use the .xlsx suffix')
    registers = projection['registers']
    from .register_content_check import AMOUNTS, check_register_content
    content_check = check_register_content(projection)
    review = [['Tax registers for review', 'AEAT import unsupported'],
              ['Scope', 'IRPF year to date; IVA selected period'],
              ['Amounts', 'EUR; source and attribution sheets are traceability only'],
              ['Calculations', 'Book entries may be excluded from returns; use inclusion flags when reconciling']]
    blockers = []
    review.extend([['Required fields', 'Checked fields ready' if content_check['checked_fields_ready'] else 'Review required'],
                   ['Source completeness', 'Not verified by this export']])
    visible_ids = set(projection['selected_row_ids'])
    parent_ids = {row['transaction_id'] for row in projection['source_rows']}
    sheets = []
    for basis in ('irpf', 'iva'):
        register = registers[basis]
        review.extend([[basis.upper() + ' from', register['starts_on']],
                       [basis.upper() + ' through', register['through']],
                       [basis.upper() + ' status', 'Blocked' if register['blocked'] else 'Amounts ready'],
                       [basis.upper() + ' rows', len(register['rows']) if not register['blocked'] else None]])
        for row in register.get('blocked_rows', []):
            blockers.append([basis.upper(), row['tax_date'], row['period'],
                             row['transaction_id'], row['source_transaction_id'], row['reason']])
        if register['blocked']:
            review.append([basis.upper() + ' reason', register['reason']])
        totals = [('Income base (EUR)', 'taxable_base_eur', {'income'}),
                  ('Expense before relief (EUR)', 'deductible_irpf_eur', {'expense', 'adjustment'}),
                  ('Withholding (EUR)', 'withholding_eur', {'income'})] if basis == 'irpf' else [
                  ('Income base (EUR)', 'taxable_base_eur', {'income'}),
                  ('Expense base (EUR)', 'taxable_base_eur', {'expense', 'adjustment'}),
                  ('VAT on income (EUR)', 'vat_eur', {'income'}),
                  ('Deductible VAT (EUR)', 'deductible_vat_eur', {'expense', 'adjustment'})]
        for label, key, kinds in totals:
            amount = None if register['blocked'] else sum(
                (row[key] or (row['amount_eur'] if basis == 'irpf' and key == 'taxable_base_eur' else Decimal(0))
                 for row in register['rows'] if row['kind'] in kinds), Decimal(0))
            review.append([basis.upper() + ' ' + label, amount])
        sheets.append((basis.upper(), _table(REGISTER_COLUMNS, register['rows'])))
    sources = sorted(projection['source_rows'], key=lambda row: (row['tax_date'], row['transaction_id']))
    sheets.append(('SOURCES', _table(SOURCE_COLUMNS, sources)))
    bridge = [['Row ID', 'Source transaction ID', 'Component', 'Attribution ID',
               'IRPF date', 'IRPF base (EUR)', 'IRPF expense (EUR)', 'IRPF error',
               'IVA date', 'IVA base (EUR)', 'IVA (EUR)', 'Deductible IVA (EUR)', 'IVA error',
               'Evidence', 'FX evidence', 'Reason', 'IRPF evidence', 'IVA evidence']]
    for row in projection['bridge']:
        if row['transaction_id'] not in visible_ids and row['transaction_id'] not in parent_ids:
            continue
        irpf, iva = row['irpf'] or {}, row['iva'] or {}
        # TaxBasis uses zero defaults for pending values; these must remain blank here.
        bridge.append([row['transaction_id'], row['source_transaction_id'], row['component'],
            row['attribution_id'], irpf.get('date'), _confirmed(irpf, 'taxable_base_eur'),
            _confirmed(irpf, 'deductible_irpf_eur'), irpf.get('error'), iva.get('date'),
            _confirmed(iva, 'taxable_base_eur'), _confirmed(iva, 'vat_eur'),
            _confirmed(iva, 'deductible_vat_eur'), iva.get('error'), row['evidence_ref'],
            row['fx_evidence_ref'], row['reason'], irpf.get('evidence_ref'), iva.get('evidence_ref')])
    sheets.extend([('ATTRIBUTIONS', bridge), ('BLOCKERS',
        [['Tax', 'Tax date', 'Affected period', 'Row ID', 'Source transaction ID', 'Reason'], *blockers])])
    sheets.append(('REQUIRED_FIELDS', [['Register', 'Row ID', 'Missing fields'],
        *[[item['register'], item['transaction_id'], ', '.join(item['missing_fields'])]
          for item in content_check['blockers']]]))
    total_rows = [['Register', 'Scope', 'Kind', *AMOUNTS]]
    for basis, totals in content_check['totals'].items():
        for quarter, kinds in sorted(totals['quarters'].items()):
            for kind, amounts in sorted(kinds.items()):
                total_rows.append([basis, quarter, kind, *[amounts[key] for key in AMOUNTS]])
        for kind, amounts in sorted(totals['scope_total'].items()):
            total_rows.append([basis, 'Selected scope', kind, *[amounts[key] for key in AMOUNTS]])
    sheets.append(('TOTALS', total_rows))
    sheets.insert(0, ('REVIEW', review))
    return write_table_workbook(target, sheets)


def write_table_workbook(path: str | Path, sheets: list[tuple[str, list[list[Any]]]], *,
                         exclusive: bool = False, max_bytes: int | None = None) -> Path:
    """Write typed value-only sheets; exclusive outputs never replace an earlier book."""
    target = Path(path)
    if target.suffix.lower() != '.xlsx':
        raise ValueError('Workbook output must use the .xlsx suffix')
    parts = _package(sheets)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.xlsx.tmp', delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in parts.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        if max_bytes is not None and temporary.stat().st_size > max_bytes:
            raise ValueError('Workbook exceeds its size limit')
        if exclusive:
            os.link(temporary, target)
        else:
            temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _confirmed(basis: Mapping[str, Any], key: str) -> Any:
    return None if basis.get('error') else basis.get(key)


def _table(columns: tuple, rows: list) -> list[list[Any]]:
    return [[label for _, label in columns],
            *[[(row[key] or row['transaction_id']) if key == 'source_transaction_id' else row[key]
               for key, _ in columns] for row in rows]]


def _column(index: int) -> str:
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xml(root: ET.Element) -> bytes:
    content = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    ET.fromstring(content)  # Reject invalid XML characters before touching the destination.
    return content


def _sheet(rows: list[list[Any]], *, summary: bool = False) -> bytes:
    if len(rows) > 1048576:
        raise ValueError('Tax register exceeds the XLSX row limit')
    root = ET.Element('worksheet', xmlns=MAIN_NS)
    width = max(len(row) for row in rows)
    end = f'{_column(width)}{len(rows)}'
    ET.SubElement(root, 'dimension', ref=f'A1:{end}')
    view = ET.SubElement(ET.SubElement(root, 'sheetViews'), 'sheetView', workbookViewId='0', showGridLines='0')
    if not summary:
        ET.SubElement(view, 'pane', xSplit='3', ySplit='1', topLeftCell='D2', activePane='bottomRight', state='frozen')
    columns = ET.SubElement(root, 'cols')
    sizes = []
    for index in range(1, width + 1):
        label = str(rows[0][index - 1])
        size = (34 if index == 1 else 100) if summary else min(64, max(18, len(label) + 3))
        if not summary and any(word in label for word in ('ID', 'SHA-256', 'Evidence', 'evidence', 'Reason', 'error')):
            size = 64
        ET.SubElement(columns, 'col', min=str(index), max=str(index), width=str(size), customWidth='1')
        sizes.append(size)
    data = ET.SubElement(root, 'sheetData')
    for row_number, values in enumerate(rows, 1):
        lines = max((sum(max(1, (len(line) + sizes[index] - 1) // sizes[index])
                         for line in str(value).split('\n'))
                     for index, value in enumerate(values) if value is not None), default=1)
        height = min(409, max(30 if row_number == 1 else 24, lines * 16 + 8))
        row = ET.SubElement(data, 'row', r=str(row_number), ht=str(height), customHeight='1')
        for index, value in enumerate(values, 1):
            if value is None:
                continue
            cell = ET.SubElement(row, 'c', r=f'{_column(index)}{row_number}')
            cell.set('s', '1' if row_number == 1 else '0')
            if isinstance(value, date):
                cell.set('s', '3')
                ET.SubElement(cell, 'v').text = str((value - date(1899, 12, 30)).days)
            elif isinstance(value, (Decimal, int)) and not isinstance(value, bool):
                if isinstance(value, Decimal):
                    if not value.is_finite():
                        raise ValueError('Tax register amounts must be finite')
                    cell.set('s', '2')
                ET.SubElement(cell, 'v').text = str(value)
            else:
                text = str(value)
                if len(text) > 32767:
                    raise ValueError('Tax register text exceeds the XLSX cell limit')
                cell.set('t', 'inlineStr')
                ET.SubElement(ET.SubElement(cell, 'is'), 't', {'xml:space': 'preserve'}).text = text
    if not summary:
        ET.SubElement(root, 'autoFilter', ref=f'A1:{end}')
    return _xml(root)


def _package(sheets: list[tuple[str, list[list[Any]]]]) -> dict[str, bytes]:
    types = ET.Element('Types', xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
    ET.SubElement(types, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    ET.SubElement(types, 'Default', Extension='xml', ContentType='application/xml')
    workbook = ET.Element('workbook', {'xmlns': MAIN_NS, 'xmlns:r': DOC_REL_NS})
    sheet_list = ET.SubElement(workbook, 'sheets')
    rels = ET.Element('Relationships', xmlns=REL_NS)
    parts = {}
    for index, (name, rows) in enumerate(sheets, 1):
        ET.SubElement(sheet_list, 'sheet', name=name, sheetId=str(index), attrib={'r:id': f'rId{index}'})
        ET.SubElement(rels, 'Relationship', Id=f'rId{index}', Type=DOC_REL_NS + '/worksheet', Target=f'worksheets/sheet{index}.xml')
        part = f'xl/worksheets/sheet{index}.xml'
        parts[part] = _sheet(rows, summary=name == 'REVIEW')
        ET.SubElement(types, 'Override', PartName='/' + part, ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
    ET.SubElement(rels, 'Relationship', Id='styles', Type=DOC_REL_NS + '/styles', Target='styles.xml')
    for name, kind in [('workbook', 'sheet.main'), ('styles', 'styles')]:
        ET.SubElement(types, 'Override', PartName=f'/xl/{name}.xml', ContentType=f'application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml')
    root_rels = ET.Element('Relationships', xmlns=REL_NS)
    ET.SubElement(root_rels, 'Relationship', Id='rId1', Type=DOC_REL_NS + '/officeDocument', Target='xl/workbook.xml')
    parts.update({'[Content_Types].xml': _xml(types), '_rels/.rels': _xml(root_rels),
                  'xl/workbook.xml': _xml(workbook), 'xl/_rels/workbook.xml.rels': _xml(rels),
                  'xl/styles.xml': STYLES.encode()})
    return parts


STYLES = f'''<styleSheet xmlns="{MAIN_NS}">
<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/><color rgb="FFFFFFFF"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF28465C"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment horizontal="right" vertical="center"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment vertical="center"/></xf>
</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
