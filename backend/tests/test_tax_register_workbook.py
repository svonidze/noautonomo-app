from datetime import date, timedelta
from decimal import Decimal
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from autonomo_taxes.aeat_books import build_aeat_book_projection
from autonomo_taxes.books import write_accounting_books
from autonomo_taxes.filing_package import write_filing_package, verify_filing_package
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.tax_attribution import apply_attribution, register_projection
from autonomo_taxes.tax_engine import calculate_modelo390, calculate_modelo100_business_support
from autonomo_taxes.tax_register_workbook import write_tax_register_review_xlsx
from autonomo_taxes.tax_row_loader import load_tax_rows
from test_tax_attribution import source, decision, calc130, calculate_modelo303_rows


def read_workbook(path):
    """Read OPC relationships and typed cells independently of the exporter."""
    result = {}
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            ET.fromstring(archive.read(name))
        rels = {r.get('Id'): r.get('Target') for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        for sheet in ET.fromstring(archive.read('xl/workbook.xml')).findall('.//{*}sheet'):
            rel = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            root = ET.fromstring(archive.read('xl/' + rels[rel]))
            rows = []
            for row in root.findall('.//{*}sheetData/{*}row'):
                values = {}
                for cell in row:
                    assert cell.find('{*}f') is None
                    ref = ''.join(c for c in cell.get('r') if c.isalpha())
                    if cell.get('t') == 'inlineStr':
                        values[ref] = ''.join(cell.find('{*}is').itertext())
                    elif cell.get('s') == '3':
                        values[ref] = date(1899, 12, 30) + timedelta(days=int(cell.find('{*}v').text))
                    else:
                        values[ref] = Decimal(cell.find('{*}v').text)
                rows.append(values)
            headers = rows[0]
            result[sheet.get('name')] = [{label: row.get(col) for col, label in headers.items()}
                                         for row in rows[1:]]
    return result


def review_values(book):
    return {row['Tax registers for review']: row['AEAT import unsupported'] for row in book['REVIEW']}


def test_year_boundary_fx_and_annual_totals_are_preserved(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path)
        apply_attribution(db, decision(db, identifier), actor='test-operator')
        db.ensure_period('2024-Q4'); db.ensure_period('2025')
        db.create_filing_snapshot('2025-Q1', status='baseline', filed_on='2025-04-11', payload={'test': True})
        before = {table: [tuple(r) for r in db.connection.execute('SELECT * FROM ' + table)]
                  for table in ('transactions', 'documents', 'tax_treatments', 'filing_snapshots')}
        old = read_workbook(write_accounting_books(db, tmp_path / 'old', period_key='2024-Q4').files['tax_registers_xlsx'])
        assert len(old['IRPF']) == 1 and old['IVA'] == []
        assert old['IRPF'][0]['Tax date'] == date(2024, 12, 20)
        assert old['IRPF'][0]['Issue date'] == date(2025, 1, 8)
        assert old['IRPF'][0]['Source transaction ID'] == identifier
        assert old['IRPF'][0]['Tax base (EUR)'] == calc130(load_tax_rows(db, 2024), 2024, 4).values['01']
        export = write_accounting_books(db, tmp_path / 'annual', period_key='2025')
        book = read_workbook(export.files['tax_registers_xlsx'])
        rows = load_tax_rows(db, 2025)
        totals = review_values(book)
        assert [r['Component'] for r in book['IRPF']] == ['fx_gain', 'fx_loss']
        assert len(book['IVA']) == 1 and book['IVA'][0]['Component'] == 'transaction'
        assert all(r['Source transaction ID'] == identifier for r in book['IRPF'])
        assert book['IVA'][0]['Tax base (EUR)'] == Decimal('935.00')
        assert totals['IRPF Income base (EUR)'] == calc130(rows, 2025, 4).values['01'] == Decimal('15.00')
        annual = calculate_modelo100_business_support(rows, year=2025, difficult_expenses_rate=Decimal('.05'))
        assert totals['IRPF Expense before relief (EUR)'] == annual.values['business_expenses_before_difficult'] == Decimal('3.00')
        reports = [calculate_modelo303_rows(rows, year=2025, quarter=q) for q in range(1, 5)]
        assert totals['IVA Income base (EUR)'] == calculate_modelo390(reports, year=2025).values['110']
        assert len(book['SOURCES']) == 1 and book['SOURCES'][0]['Source transaction ID'] == identifier
        assert book['SOURCES'][0]['Source amount (EUR)'] == Decimal('940.00')
        assert book['SOURCES'][0]['Document SHA-256'] == book['IVA'][0]['Document SHA-256']
        assert {r['FX evidence'] for r in book['IRPF']} == {'document:fx-a', 'document:fx-b'}
        for table, original in before.items():
            assert [tuple(r) for r in db.connection.execute('SELECT * FROM ' + table)] == original


def test_late_invoice_uses_independent_quarter_windows(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path, day='2025-10-09')
        p = decision(db, identifier, irpf_date='2025-09-22', iva_date='2025-11-10')
        p['decision']['fx'] = []
        apply_attribution(db, p, actor='test-operator')
        db.ensure_period('2025-Q3')
        q3 = read_workbook(write_accounting_books(db, tmp_path / 'q3', period_key='2025-Q3').files['tax_registers_xlsx'])
        q4 = read_workbook(write_accounting_books(db, tmp_path / 'q4', period_key='2025-Q4').files['tax_registers_xlsx'])
        assert len(q3['IRPF']) == len(q4['IRPF']) == len(q4['IVA']) == 1
        assert q3['IVA'] == []
        assert q3['IRPF'][0]['Tax base (EUR)'] == Decimal('920.00')
        assert q4['IVA'][0]['Tax base (EUR)'] == Decimal('935.00')
        assert review_values(q4)['IRPF from'] == date(2025, 1, 1)
        assert review_values(q4)['IVA from'] == date(2025, 10, 1)
        assert q4['IVA'][0]['Tax base (EUR)'] == calculate_modelo303_rows(load_tax_rows(db, 2025), year=2025, quarter=4).values['120']


@pytest.mark.parametrize('stale', [False, True])
def test_blocked_amounts_are_blank_with_date_period_reason_and_manifest(tmp_path, stale):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path, day='2025-10-09')
        p = decision(db, identifier, irpf_date='2025-09-22', iva_date='2025-11-10')
        p['decision']['fx'] = []
        if not stale:
            p['decision']['iva'].update(base_minor=None, vat_minor=None, deductible_minor=None,
                                       pending_reason='Await reviewed currency observation')
        apply_attribution(db, p, actor='test-operator')
        if stale:
            db.connection.execute('UPDATE tax_treatments SET taxable_base_minor=95000 WHERE transaction_id=?', (identifier,))
        db.ensure_period('2025-Q3')
        manifest_path = write_filing_package(db, tmp_path / 'package', period_key='2025-Q4')
        assert verify_filing_package(db, manifest_path)['ok']
        manifest = json.loads(manifest_path.read_text())
        assert manifest['tax_register_xlsx']['registers_complete'] is False
        assert manifest['tax_register_xlsx']['aeat_import_supported'] is False
        assert 'tax_attribution_pending' in manifest['readiness_blockers']
        book = read_workbook(tmp_path / 'package/tax-registers-review.xlsx')
        assert book['IVA'] == [] and review_values(book)['IVA Income base (EUR)'] is None
        error = 'Source facts changed' if stale else 'Await reviewed currency observation'
        iva_blocker = next(r for r in book['BLOCKERS'] if r['Tax'] == 'IVA')
        assert iva_blocker['Tax date'] == date(2025, 11, 10)
        assert iva_blocker['Affected period'] == '2025-Q4' and error in iva_blocker['Reason']
        bridge = book['ATTRIBUTIONS'][0]
        assert bridge['IVA base (EUR)'] is None and bridge['IVA (EUR)'] is None
        assert error in bridge['IVA error']
        if not stale:
            assert review_values(book)['IRPF Income base (EUR)'] == Decimal('920.00')
            q3 = read_workbook(write_accounting_books(db, tmp_path / 'q3', period_key='2025-Q3').files['tax_registers_xlsx'])
            assert review_values(q3)['IVA status'] == 'Complete' and q3['BLOCKERS'] == []
        aeat = build_aeat_book_projection(db, period_key='2025-Q4')
        assert aeat['separate_register_xlsx']['generation_supported'] is True
        assert aeat['separate_register_xlsx']['registers_complete'] is False
        assert aeat['xlsx_ready'] is False and aeat['blockers'][0]['code'] == 'separate_tax_registers_required'
        assert aeat['separate_register_xlsx']['scope']['iva']['starts_on'] == date(2025, 10, 1)


def test_unattributed_exports_keep_existing_files(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        source(db, tmp_path)
        export = write_accounting_books(db, tmp_path / 'books', period_key='2025-Q1')
        assert set(export.files) == {'income', 'expense', 'assets', 'payments'}


def test_expense_keeps_independent_deductions_and_ordinary_rows(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path)
        db.connection.execute("UPDATE transactions SET entry_type='expense' WHERE transaction_id=?", (identifier,))
        db.connection.execute("""UPDATE tax_treatments SET tax_code='domestic_input',
            deductible_irpf_minor=70000, vat_minor=16000, deductible_vat_minor=16000
            WHERE transaction_id=?""", (identifier,))
        p = decision(db, identifier, irpf_date='2025-01-22', iva_date='2025-04-15')
        p['decision']['irpf'].update(income_minor=0, deductible_minor=60000)
        p['decision']['iva'].update(base_minor=82000, vat_minor=17220, deductible_minor=8600)
        p['decision']['fx'] = []
        apply_attribution(db, p, actor='test-operator')
        # An ordinary row must still appear once alongside the attributed expense.
        ordinary = db.add_transaction(period_key='2025-Q2', transaction_date='2025-04-08',
            booking_date='2025-04-08', entry_type='income', description='Synthetic ordinary income',
            amount_minor=21000, currency='EUR', lifecycle_status='posted')
        db.add_detailed_tax_treatment(transaction_id=ordinary['transaction_id'],
            treatment_type='invoice_review', tax_code='outside_scope', taxable_base_minor=21000,
            include_modelo130=True, include_modelo303=True)
        export = write_accounting_books(db, tmp_path / 'books', period_key='2025-Q2')
        book = read_workbook(export.files['tax_registers_xlsx'])
        rows = load_tax_rows(db, 2025)
        totals = review_values(book)
        assert len(book['IRPF']) == len(book['IVA']) == len(book['SOURCES']) == 2
        assert totals['IRPF Expense before relief (EUR)'] == calc130(rows, 2025, 2).values['02'] - calc130(rows, 2025, 2).values['difficult_expenses'] == Decimal('600.00')
        report = calculate_modelo303_rows(rows, year=2025, quarter=2)
        assert totals['IVA Expense base (EUR)'] == report.values['28'] == Decimal('820.00')
        assert totals['IVA Deductible VAT (EUR)'] == report.values['29'] == Decimal('86.00')
        assert next(r for r in book['IVA'] if r['Kind'] == 'expense')['VAT (EUR)'] == Decimal('172.20')


def test_fx_in_a_later_year_keeps_parent_and_excludes_unselected_events(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path)
        p = decision(db, identifier)
        p['decision']['fx'][0]['date'] = '2027-02-09'
        apply_attribution(db, p, actor='test-operator')
        db.ensure_period('2027-Q1')
        book = read_workbook(write_accounting_books(db, tmp_path / 'books', period_key='2027-Q1').files['tax_registers_xlsx'])
        assert len(book['IRPF']) == 1 and book['IVA'] == []
        assert len(book['SOURCES']) == 1
        assert {r['Component'] for r in book['ATTRIBUTIONS']} == {'transaction', 'fx_gain'}
        assert review_values(book)['IRPF Income base (EUR)'] == Decimal('15.00')


def test_workbook_is_deterministic_literal_text_and_atomically_rejects_invalid_xml(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path)
        p = decision(db, identifier, irpf_date='2025-01-20')
        p['decision']['fx'] = []
        apply_attribution(db, p, actor='test-operator')
        projection = register_projection(load_tax_rows(db, 2025), year=2025, through=date(2025, 3, 31))
        projection['registers']['irpf']['rows'][0]['document_number'] = '=HYPERLINK("https://example.invalid")'
        path = write_tax_register_review_xlsx(tmp_path / 'review.xlsx', projection)
        before = path.read_bytes()
        assert write_tax_register_review_xlsx(path, projection).read_bytes() == before
        assert read_workbook(path)['IRPF'][0]['Document number'].startswith('=HYPERLINK')
        projection['registers']['irpf']['rows'][0]['document_number'] = 'invalid\x01text'
        with pytest.raises(ET.ParseError):
            write_tax_register_review_xlsx(path, projection)
        assert path.read_bytes() == before
