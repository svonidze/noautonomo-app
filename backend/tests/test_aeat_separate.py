from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from autonomo_taxes.aeat_separate import build_separate_projection, read_registration_act, write_separate_xlsx
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.tax_register_workbook import write_table_workbook
from autonomo_test_support.aeat_books import _profile_and_activity
from test_tax_attribution import source, decision
from autonomo_taxes.tax_attribution import apply_attribution


def book_fixture(db, tmp_path):
    activity = _profile_and_activity(db)
    income = source(db, tmp_path, day='2025-04-08')
    db.connection.execute('UPDATE transactions SET business_activity_id=?', (activity['business_activity_id'],))
    party = db.connection.execute('SELECT counterparty_id FROM transactions WHERE transaction_id=?',(income,)).fetchone()[0]
    db.upsert_counterparty_identity(counterparty_id=party, identity_kind='other_proof',country_code='US',
        identifier='SYNTH-CLIENT',source_reference='synthetic:contract',source_hash='synthetic:identity')
    supplier = db.upsert_counterparty(display_name='Synthetic Supplier',country_code='ES',tax_id='TEST-TAX-ID-001')
    for i,day in enumerate(['2025-02-01','2025-05-01'],1):
        doc = db.upsert_document(document_type='expense_invoice',document_number=f'SYNTH-{i}',
            issued_on=day,period_key=f'2025-Q{i}',currency='EUR',total_minor=12100,
            lifecycle_status='approved',source_hash=f'synthetic:invoice:{i}',counterparty_id=supplier['counterparty_id'])
        tx = db.add_transaction(period_key=f'2025-Q{i}',transaction_date=day,booking_date=day,
            entry_type='expense',description='Synthetic expense',amount_minor=12100,direction='debit',
            lifecycle_status='posted',document_id=doc['document_id'],counterparty_id=supplier['counterparty_id'],
            business_activity_id=activity['business_activity_id'])
        db.add_detailed_tax_treatment(transaction_id=tx['transaction_id'],treatment_type='expense',
            tax_code='domestic_input',taxable_base_minor=10000,vat_minor=2100,deductible_vat_minor=0,
            deductible_irpf_minor=12100,withholding_minor=0,rate_basis_points=2100,
            aeat_expense_concept='G19',aeat_invoice_type='F1',aeat_operation_key='01',aeat_reverse_charge=False,
            include_modelo130=False,include_modelo303=False)
    return income


def act_for(projection):
    return {'status':'approved','approved_by':'Synthetic reviewer','origin':'reconstructed',
        'file_sha256':'a'*64,'entries':[dict(entry,number=f'SYNTH-{i:06d}')
        for i,entry in enumerate(projection['registration_requirements'],1)]}


def test_separate_books_are_cumulative_keep_excluded_invoices_and_tax_exclusive_fields(tmp_path):
    with initialize(tmp_path/'db.sqlite') as db:
        book_fixture(db,tmp_path)
        before='\n'.join(db.connection.iterdump())
        first=build_separate_projection(db,period_key='2025-Q2',tax='iva')
        assert not first['ready'] and len(first['expense_rows'])==2
        act=act_for(first)
        iva=build_separate_projection(db,period_key='2025-Q2',tax='iva',registrations=act)
        irpf=build_separate_projection(db,period_key='2025-Q2',tax='irpf',registrations=act)
        assert iva['ready'],iva['blockers']
        assert irpf['ready'],irpf['blockers']
        assert {r['autoliquidacion_periodo'] for r in iva['expense_rows']}=={'1T','2T'}
        assert all(r['gasto_deducible_eur']=='' for r in iva['expense_rows'])
        assert len(irpf['expense_rows'])==4
        ordinary=[r for r in irpf['expense_rows'] if r['concepto_gasto']=='G19']
        vat=[r for r in irpf['expense_rows'] if r['concepto_gasto']=='G25']
        assert all(r['cuota_iva_soportado_eur']=='21.00' and r['gasto_deducible_eur']=='100.00' for r in ordinary)
        assert all(r['cuota_iva_soportado_eur']=='0.00' and r['gasto_deducible_eur']=='21.00' for r in vat)
        assert {r['numero_recepcion'] for r in ordinary}=={r['numero_recepcion'] for r in vat}
        assert sum(Decimal(r['gasto_deducible_eur']) for r in irpf['expense_rows'])==Decimal('242.00')
        assert sum(Decimal(r['total_factura_eur']) for r in irpf['expense_rows'])==Decimal('242.00')
        assert all(r['clave_operacion']=='' for r in irpf['expense_rows'])
        assert len(iva['source_row_ids'])==len(iva['lineage'])==3
        assert '\n'.join(db.connection.iterdump())==before
        act['entries'][0]['source_hash']='stale'
        assert not build_separate_projection(db,period_key='2025-Q2',tax='iva',registrations=act)['ready']


def test_attributed_operation_date_requires_evidence_and_fx_own_number(tmp_path):
    with initialize(tmp_path/'db.sqlite') as db:
        income=book_fixture(db,tmp_path)
        packet=decision(db,income,irpf_date='2025-03-31',iva_date='2025-04-15')
        apply_attribution(db,packet,actor='synthetic reviewer')
        draft=build_separate_projection(db,period_key='2025-Q2',tax='irpf')
        assert any('operation date' in b['reason'] for b in draft['blockers'])
        row=next(r for r in db.list_tax_rows() if r['transaction_id']==income)
        dates={income:{'irpf':{'operation_on':'2025-03-31','source_hash':row['document_source_hash'],
                            'evidence_ref':'synthetic:service-end'}}}
        act=act_for(draft)
        projection=build_separate_projection(db,period_key='2025-Q2',tax='irpf',registrations=act,operation_dates=dates)
        assert projection['ready'],projection['blockers']
        main=next(r for r in projection['income_rows'] if r['tipo_factura']=='F1')
        assert main['fecha_expedicion']=='08/04/2025' and main['fecha_operacion']=='31/03/2025'
        gain=next(r for r in projection['income_rows'] if r['tipo_factura']=='SF')
        assert gain['concepto_ingreso']=='I07' and gain['factura_numero'].startswith('SYNTH-')
        loss=next(r for r in projection['expense_rows'] if r['tipo_factura']=='SF')
        assert loss['concepto_gasto']=='G24' and loss['numero_recepcion'].startswith('SYNTH-')


def test_registration_act_requires_approval_hash_and_independent_series(tmp_path):
    p=tmp_path/'act.json'
    act={'schema_version':1,'status':'approved','approved_by':'Synthetic reviewer','origin':'reconstructed',
        'assigned_on':'2025-07-01','entries':[
            {'entry_key':'invoice:1','source_hash':'x','number':'R-1','ordinal':1,'series':'R',
             'kind':'received_invoice','book_year':2025,'source_date':'2025-01-01'},
            {'entry_key':'sf:1','source_hash':'y','number':'R-2','ordinal':2,'series':'R',
             'kind':'sf_expense','book_year':2025,'source_date':'2025-01-01'}]}
    p.write_text(json.dumps(act));sha=hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='independent'):read_registration_act(p,expected_sha256=sha)
    with pytest.raises(ValueError,match='hash'):read_registration_act(p,expected_sha256='0'*64)
    act['entries'][1].update(series='SF',ordinal=1)
    p.write_text(json.dumps(act));sha=hashlib.sha256(p.read_bytes()).hexdigest()
    assert read_registration_act(p,expected_sha256=sha)['file_sha256']==sha


def test_value_workbook_never_overwrites_and_preserves_identifier_and_literal_text(tmp_path):
    target=tmp_path/'book.xlsx'
    sheets=[('GASTOS',[['Number','Amount','Date'],['000001',Decimal('1.23'),date(2025,1,1)],['=not-a-formula',0,None]])]
    write_table_workbook(target,sheets,exclusive=True)
    before=target.read_bytes()
    with pytest.raises(FileExistsError):write_table_workbook(target,sheets,exclusive=True)
    assert target.read_bytes()==before
    with zipfile.ZipFile(target) as z:
        xml=z.read('xl/worksheets/sheet1.xml')
        assert b'000001' in xml and b'=not-a-formula' in xml and b'<f>' not in xml
    with pytest.raises(ValueError,match='size limit'):write_table_workbook(tmp_path/'large.xlsx',sheets,exclusive=True,max_bytes=1)
    assert not (tmp_path/'large.xlsx').exists()


def test_attributed_expense_fails_closed_until_component_mapping_is_reviewed(tmp_path):
    with initialize(tmp_path/'db.sqlite') as db:
        book_fixture(db,tmp_path)
        identifier=db.connection.execute("SELECT transaction_id FROM transactions WHERE entry_type='expense' LIMIT 1").fetchone()[0]
        db.connection.execute('UPDATE tax_treatments SET include_modelo130=1,include_modelo303=1 WHERE transaction_id=?',(identifier,))
        packet=decision(db,identifier,irpf_date='2025-02-01',iva_date='2025-02-01')
        packet['decision']['irpf'].update(income_minor=0,deductible_minor=9000)
        packet['decision']['iva'].update(base_minor=9000,vat_minor=1890,deductible_minor=1000)
        packet['decision']['fx']=[]
        apply_attribution(db,packet,actor='synthetic')
        for tax in ['iva','irpf']:
            draft=build_separate_projection(db,period_key='2025-Q2',tax=tax)
            assert not draft['ready']
            assert any('Attributed expense' in b['reason'] for b in draft['blockers'])


def test_numbering_binds_scope_and_cannot_collide_with_database_receipts(tmp_path):
    from autonomo_taxes.receipt_register import create_series,register_receipt
    with initialize(tmp_path/'db.sqlite') as db:
        book_fixture(db,tmp_path)
        draft=build_separate_projection(db,period_key='2025-Q2',tax='iva')
        act=act_for(draft)
        act['entries'][0]['book_year']=2024
        bad=build_separate_projection(db,period_key='2025-Q2',tax='iva',registrations=act)
        assert any('scope differs' in b['reason'] for b in bad['blockers'])
        act=act_for(draft)
        first,second=act['entries']
        activity=db.list_business_activities()[0]
        series=create_series(db,business_activity_id=activity['business_activity_id'],tax_year=2025,
            code='R',first_number=1,reason='Synthetic fixture',evidence_ref='synthetic:series')
        register_receipt(db,document_id=first['entry_key'].removeprefix('invoice:'),series_id=series['series_id'],
            registered_on='2025-02-01',receipt_number=first['number'],evidence_ref='synthetic:receipt')
        second['number']=first['number'];act['entries']=[second]
        bad=build_separate_projection(db,period_key='2025-Q2',tax='iva',registrations=act)
        assert not bad['ready'] and any('already assigned' in b['reason'] for b in bad['blockers'])


def test_non_invoice_document_revocation_blocks_separate_book(tmp_path):
    with initialize(tmp_path/'db.sqlite') as db:
        book_fixture(db,tmp_path)
        db.connection.execute("UPDATE documents SET document_type='bank_fee_evidence',lifecycle_status='needs_review' WHERE document_type='expense_invoice'")
        draft=build_separate_projection(db,period_key='2025-Q2',tax='irpf')
        assert not draft['ready']
        assert any('source document is not approved' in b['reason'] for b in draft['blockers'])


def test_cli_retains_blocked_manifest_and_refuses_overwrite(tmp_path,monkeypatch):
    from argparse import Namespace
    from autonomo_taxes.operational_cli import _cmd_books_aeat_separate_write
    with initialize(tmp_path/'db.sqlite') as db:book_fixture(db,tmp_path)
    template=tmp_path/'template.xlsx';template.write_bytes(b'synthetic-placeholder')
    args=Namespace(db=tmp_path/'db.sqlite',period='2025-Q2',tax='iva',registrations=None,
        registrations_sha256=None,operation_dates=None,operation_dates_sha256=None,
        template=template,allow_authoritative_history=False,out_dir=tmp_path/'blocked')
    before=args.db.read_bytes()
    assert _cmd_books_aeat_separate_write(args)==2
    manifest=json.loads((args.out_dir/'manifest.json').read_text())
    assert manifest['status']=='blocked' and manifest['workbook_sha256'] is None
    assert not manifest['filed_returns_changed'] and args.db.read_bytes()==before
    with pytest.raises(FileExistsError):_cmd_books_aeat_separate_write(args)
    with initialize(args.db) as db:
        draft=build_separate_projection(db,period_key='2025-Q2',tax='iva')
    monkeypatch.setattr('autonomo_taxes.aeat_separate.read_registration_act',lambda *a,**k:act_for(draft))
    args.registrations=tmp_path/'act.json';args.registrations_sha256='synthetic';args.out_dir=tmp_path/'failed-write'
    assert _cmd_books_aeat_separate_write(args)==2
    manifest=json.loads((args.out_dir/'manifest.json').read_text())
    assert manifest['status']=='blocked' and manifest['write_error']
    assert not list(args.out_dir.glob('*.xlsx'))


def test_stale_retained_receipt_cannot_be_replaced_by_a_new_act(tmp_path):
    from autonomo_taxes.receipt_register import create_series,register_receipt
    with initialize(tmp_path/'db.sqlite') as db:
        book_fixture(db,tmp_path)
        first=build_separate_projection(db,period_key='2025-Q2',tax='iva')['registration_requirements'][0]
        document_id=first['entry_key'].removeprefix('invoice:')
        series=create_series(db,business_activity_id=db.list_business_activities()[0]['business_activity_id'],
            tax_year=2025,code='R',first_number=1,reason='Synthetic',evidence_ref='synthetic:series')
        register_receipt(db,document_id=document_id,series_id=series['series_id'],registered_on='2025-02-01',
            receipt_number='OLD-1',evidence_ref='synthetic:receipt')
        db.connection.execute("UPDATE documents SET source_hash='changed' WHERE document_id=?",(document_id,))
        draft=build_separate_projection(db,period_key='2025-Q2',tax='iva')
        result=build_separate_projection(db,period_key='2025-Q2',tax='iva',registrations=act_for(draft))
        assert not result['ready'] and any('renumbering is prohibited' in b['reason'] for b in result['blockers'])


def test_effective_income_withholding_rate_is_recomputed(tmp_path):
    with initialize(tmp_path/'db.sqlite') as db:
        identifier=book_fixture(db,tmp_path)
        packet=decision(db,identifier,irpf_date='2025-04-08',iva_date='2025-04-08')
        packet['decision']['irpf']['withholding_minor']=9200;packet['decision']['fx']=[]
        apply_attribution(db,packet,actor='synthetic')
        draft=build_separate_projection(db,period_key='2025-Q2',tax='irpf')
        result=build_separate_projection(db,period_key='2025-Q2',tax='irpf',registrations=act_for(draft))
        assert result['ready'],result['blockers']
        assert result['income_rows'][0]['tipo_retencion_irpf_percent']=='10.00'


@pytest.mark.parametrize('failure',['manifest','workbook_hash'])
def test_cli_removes_workbook_if_final_evidence_cannot_be_written(tmp_path,monkeypatch,failure):
    from argparse import Namespace
    from autonomo_taxes.operational_cli import _cmd_books_aeat_separate_write
    with initialize(tmp_path/'db.sqlite') as db:
        book_fixture(db,tmp_path)
        draft=build_separate_projection(db,period_key='2025-Q2',tax='iva')
    monkeypatch.setattr('autonomo_taxes.aeat_separate.read_registration_act',lambda *a,**k:act_for(draft))
    def writer(template,projection,path):path.write_bytes(b'synthetic-workbook');return path
    monkeypatch.setattr('autonomo_taxes.aeat_separate.write_separate_xlsx',writer)
    template=tmp_path/'template.xlsx';template.write_bytes(b'synthetic-template')
    original_write=Path.write_text;original_read=Path.read_bytes
    def failing_write(path,*a,**kw):
        if path.name=='manifest.json':raise OSError('injected manifest failure')
        return original_write(path,*a,**kw)
    def failing_read(path):
        if path.name.startswith('2025'):raise OSError('injected hash failure')
        return original_read(path)
    if failure=='manifest':monkeypatch.setattr(Path,'write_text',failing_write)
    else:monkeypatch.setattr(Path,'read_bytes',failing_read)
    args=Namespace(db=tmp_path/'db.sqlite',period='2025-Q2',tax='iva',registrations=tmp_path/'act.json',
        registrations_sha256='synthetic',operation_dates=None,operation_dates_sha256=None,
        template=template,allow_authoritative_history=False,out_dir=tmp_path/'output')
    with pytest.raises(OSError,match='injected'):_cmd_books_aeat_separate_write(args)
    assert not list(args.out_dir.glob('*.xlsx'))


def test_native_capital_invoice_and_depreciation_are_not_double_counted(tmp_path):
    from autonomo_test_support.expense_workflow import setup_draft
    from autonomo_taxes.ledger_db import open as open_db
    from test_expense_workflow import commit
    fixture,draft=setup_draft(tmp_path,method='immediate',transaction_date='2025-01-02')
    with open_db(fixture['database']) as db:
        commit(db,fixture,draft,tmp_path)
        db.connection.execute("UPDATE counterparties SET tax_id='TEST-TAX-ID-001'")
        db.connection.execute("UPDATE tax_treatments SET aeat_expense_concept=NULL WHERE transaction_id=?",(fixture['transaction_id'],))
        first=build_separate_projection(db,period_key='2025-Q1',tax='irpf')
        result=build_separate_projection(db,period_key='2025-Q1',tax='irpf',registrations=act_for(first))
        assert result['ready'],result['blockers']
        invoice=next(r for r in result['expense_rows'] if r['tipo_factura']=='F1')
        depreciation=next(r for r in result['expense_rows'] if r['tipo_factura']=='SF')
        assert invoice['gasto_deducible_eur']=='0.00' and invoice['concepto_gasto']==''
        assert Decimal(depreciation['gasto_deducible_eur'])==100
        assert depreciation['concepto_gasto']=='G31' and Decimal(depreciation['base_imponible_eur'])==100
        assert len(result['asset_rows'])==1 and result['asset_rows'][0]['numero_recepcion']==invoice['numero_recepcion']
