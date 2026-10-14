from dataclasses import replace
from datetime import date
from decimal import Decimal
import hashlib
import json
import sqlite3

import pytest

from autonomo_taxes.ledger_db import initialize, open_database, LATEST_SCHEMA_VERSION
from autonomo_taxes.tax_attribution import (
    TaxAttributionError, apply_attribution, latest_attribution, prepare_attribution, register_projection,
)
from autonomo_taxes.tax_engine import (
    CalculationBlocked, calculate_modelo130_rows, calculate_modelo303_rows,
    calculate_modelo100_business_support,
)
from autonomo_taxes.tax_row_loader import load_tax_rows


def source(db, tmp_path, *, day='2025-01-08', status='posted'):
    evidence = tmp_path / 'synthetic-source.txt'
    evidence.write_text('Independently invented accounting fixture, no operational data.')
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    period = f'{day[:4]}-Q{(date.fromisoformat(day).month - 1) // 3 + 1}'
    party = db.upsert_counterparty(display_name='Example North Services', country_code='US')
    doc = db.upsert_document(external_key='sha256:' + digest, document_type='income_invoice',
        document_number='DEMO-NORTH-41', issued_on=day, period_key=period, currency='EUR',
        total_minor=94000, lifecycle_status='approved', source_hash=digest,
        counterparty_id=party['counterparty_id'])
    db.set_document_storage(doc['document_id'], source_path=str(evidence), mime_type='text/plain')
    tx = db.add_transaction(period_key=period, transaction_date=day, booking_date=day,
        entry_type='income', description='Synthetic monthly service', amount_minor=94000,
        currency='EUR', lifecycle_status=status, document_id=doc['document_id'],
        counterparty_id=party['counterparty_id'])
    db.add_detailed_tax_treatment(transaction_id=tx['transaction_id'], treatment_type='invoice_review',
        tax_code='outside_scope', taxable_base_minor=94000, vat_minor=0, deductible_irpf_minor=0,
        deductible_vat_minor=0, withholding_minor=0, include_modelo130=True, include_modelo303=True,
        aeat_invoice_type='F1', aeat_operation_key='01', aeat_operation_qualification='N2')
    return tx['transaction_id']


def decision(db, identifier, *, irpf_date='2024-12-20', iva_date='2025-01-12'):
    p = prepare_attribution(db, identifier)
    p.update(reason='Reviewed dates and independent conversion evidence', evidence_ref='document:synthetic-proof')
    p['decision'] = {
        'irpf': {'date': irpf_date, 'income_minor': 92000, 'deductible_minor': 0,
                 'withholding_minor': 0, 'evidence_ref': 'document:synthetic-accrual'},
        'iva': {'date': iva_date, 'base_minor': 93500, 'vat_minor': 0, 'deductible_minor': 0,
                'pending_reason': None, 'evidence_ref': 'document:synthetic-vat'},
        'fx': [
            {'event_key': 'receipt-a', 'date': '2025-01-17', 'amount_minor': 1500, 'evidence_ref': 'document:fx-a'},
            {'event_key': 'receipt-b', 'date': '2025-02-15', 'amount_minor': -300, 'evidence_ref': 'document:fx-b'},
        ],
    }
    return p


def calc130(rows, year, quarter=1):
    return calculate_modelo130_rows(rows, year=year, quarter=quarter,
                                   difficult_expenses_rate=Decimal('.05'))[1]


def test_cross_year_and_fx_pull_parent_without_rewriting_history(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path)
        before = tuple(db.connection.execute('SELECT * FROM transactions').fetchone())
        p = decision(db, identifier)
        saved = apply_attribution(db, p, actor='test-operator')
        assert apply_attribution(db, p, actor='test-operator')['attribution_id'] == saved['attribution_id']
        assert tuple(db.connection.execute('SELECT * FROM transactions').fetchone()) == before
        old_year = load_tax_rows(db, 2024)
        new_year = load_tax_rows(db, 2025)
        assert calc130(old_year, 2024, 4).values['01'] == Decimal('920.00')
        assert calc130(new_year, 2025).values['01'] == Decimal('15.00')
        assert calc130(new_year, 2025).values['02'] == Decimal('3.60')
        assert calc130(new_year, 2025).values['19'] == Decimal('2.28')
        assert calculate_modelo303_rows(new_year, year=2025, quarter=1).values['120'] == Decimal('935.00')
        assert len(new_year) == 3
        assert all(r.parent_id == identifier for r in new_year)
        assert calc130(new_year, 2025).lineage['02'] == (identifier,)
        # The prior-year principal is still visible to an FX event in a later year.
        p = prepare_attribution(db, identifier)
        p['decision']['fx'][0]['date'] = '2027-02-09'
        apply_attribution(db, p, actor='test-operator')
        assert calc130(load_tax_rows(db, 2027), 2027).values['01'] == Decimal('15.00')
        assert calc130(load_tax_rows(db, 2025), 2025).values['01'] == Decimal('0.00')


def test_late_invoice_cutoff_and_pending_vat_are_independent(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path, day='2025-10-09')
        p = decision(db, identifier, irpf_date='2025-09-22', iva_date='2025-11-10')
        p['decision']['fx'] = []
        p['decision']['iva'].update(base_minor=None, vat_minor=None, deductible_minor=None,
                                     pending_reason='Await reviewed currency observation')
        apply_attribution(db, p, actor='test-operator')
        rows = load_tax_rows(db, 2025, through=date(2025, 9, 30))
        assert len(rows) == 1
        assert calc130(rows, 2025, 3).values['01'] == Decimal('920.00')
        assert calculate_modelo100_business_support(rows, year=2025,
            difficult_expenses_rate=Decimal('.05')).values['business_income'] == Decimal('920.00')
        assert calculate_modelo303_rows(rows, year=2025, quarter=3).values['120'] == 0
        all_year = load_tax_rows(db, 2025)
        assert calc130(all_year, 2025, 4).values['01'] == Decimal('920.00')
        with pytest.raises(CalculationBlocked, match='IVA amounts pending'):
            calculate_modelo303_rows(all_year, year=2025, quarter=4)
        p = prepare_attribution(db, identifier)
        p['decision']['irpf']['date'] = '2025-12-10'
        p['decision']['iva'].update(date='2025-01-12', base_minor=93500, vat_minor=0,
                                     deductible_minor=0, pending_reason=None)
        apply_attribution(db, p, actor='test-operator')
        through = load_tax_rows(db, 2025, through=date(2025, 3, 31))
        assert calculate_modelo100_business_support(through, year=2025,
            difficult_expenses_rate=Decimal('.05')).values['business_income'] == 0
        assert calculate_modelo303_rows(through, year=2025, quarter=1).values['120'] == Decimal('935.00')


def test_revision_guards_and_schema_fx_uniqueness(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path)
        p = decision(db, identifier, irpf_date='2025-01-20')
        saved = apply_attribution(db, p, actor='test-operator')
        old_total = calc130(load_tax_rows(db, 2025), 2025).values['19']
        stale = json.loads(json.dumps(p)); stale['decision']['irpf']['income_minor'] += 10000
        with pytest.raises(TaxAttributionError, match='Attribution changed'):
            apply_attribution(db, stale, actor='test-operator')
        fresh = prepare_attribution(db, identifier); fresh['decision']['irpf']['income_minor'] += 10000
        apply_attribution(db, fresh, actor='test-operator')
        assert calc130(load_tax_rows(db, 2025), 2025).values['19'] - old_total == Decimal('19.00')
        assert db.connection.execute('SELECT COUNT(*) FROM transaction_tax_attributions').fetchone()[0] == 2
        assert len(load_tax_rows(db, 2025)) == 3
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            db.connection.execute("UPDATE transaction_tax_attributions SET reason='changed'")
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute('INSERT INTO tax_attribution_fx_events SELECT * FROM tax_attribution_fx_events LIMIT 1')
        source_stale = prepare_attribution(db, identifier)
        db.connection.execute('UPDATE tax_treatments SET taxable_base_minor=95000 WHERE transaction_id=?', (identifier,))
        with pytest.raises(TaxAttributionError, match='Source facts changed'):
            apply_attribution(db, source_stale, actor='test-operator')
        with pytest.raises(CalculationBlocked, match='Source facts changed'):
            calc130(load_tax_rows(db, 2025), 2025)


@pytest.mark.parametrize('change', [
    lambda p: p['decision']['irpf'].update(income_minor=True),
    lambda p: p['decision']['irpf'].update(date='20250120'),
    lambda p: p['decision']['iva'].update(deductible_minor=1),
    lambda p: p['decision']['fx'].append(dict(p['decision']['fx'][0])),
    lambda p: p['decision']['fx'][0].update(amount_minor=0),
    lambda p: p.update(reason=''),
])
def test_invalid_decision_is_atomic(tmp_path, change):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path); p = decision(db, identifier); change(p)
        with pytest.raises(TaxAttributionError): apply_attribution(db, p, actor='test-operator')
        assert latest_attribution(db, identifier) is None
        assert db.connection.execute('SELECT COUNT(*) FROM tax_attribution_fx_events').fetchone()[0] == 0


def test_closed_period_requires_ack_and_snapshots_are_immutable(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path)
        db.connection.execute("UPDATE periods SET status='closed' WHERE period_key='2025-Q1'")
        snapshot = db.create_filing_snapshot('2025-Q1', status='baseline', filed_on='2025-04-11',
                                           payload={'form': '130', 'values': {'19': '88.00'}})
        before = tuple(db.connection.execute('SELECT * FROM filing_snapshots').fetchone())
        p = decision(db, identifier)
        with pytest.raises(TaxAttributionError, match='acknowledgement'):
            apply_attribution(db, p, actor='test-operator')
        p['historical_correction'] = True
        apply_attribution(db, p, actor='test-operator')
        assert tuple(db.connection.execute('SELECT * FROM filing_snapshots').fetchone()) == before
        assert 'corrected calculation' in calc130(load_tax_rows(db, 2025), 2025).warnings[0]


def test_approved_parent_is_only_in_explicit_effective_period_forecast(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path, day='2025-10-09', status='approved')
        p = decision(db, identifier, irpf_date='2025-09-22', iva_date='2025-10-09')
        p['decision']['fx'] = []
        apply_attribution(db, p, actor='test-operator')
        assert load_tax_rows(db, 2025) == []
        forecast = load_tax_rows(db, 2025, include_approved_periods={'2025-Q3'})
        assert calc130(forecast, 2025, 3).values['01'] == Decimal('920.00')


def test_registers_have_effective_amounts_and_original_bridge(tmp_path):
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path)
        apply_attribution(db, decision(db, identifier), actor='test-operator')
        p = register_projection(load_tax_rows(db, 2025), year=2025, through=date(2025, 3, 31))
        assert len(p['registers']['irpf']['rows']) == 2
        assert len(p['registers']['iva']['rows']) == 1
        assert p['registers']['iva']['rows'][0]['taxable_base_eur'] == Decimal('935.00')
        original = next(r for r in p['bridge'] if r['component'] == 'transaction')
        assert original['original_amount_eur'] == Decimal('940.00')
        assert original['irpf']['taxable_base_eur'] == Decimal('920.00')


def test_additive_migration_keeps_all_existing_financial_rows(tmp_path):
    path = tmp_path / 'db.sqlite'
    with initialize(path) as db:
        identifier = source(db, tmp_path)
        before = {table: [tuple(r) for r in db.connection.execute('SELECT * FROM ' + table)]
                  for table in ('transactions', 'documents', 'tax_treatments', 'payments', 'filing_snapshots')}
        db.connection.execute('DROP TABLE tax_attribution_fx_events')
        db.connection.execute('DROP TABLE transaction_tax_attributions')
        db.connection.execute('PRAGMA user_version=27')
    with open_database(path, apply_migrations=True) as db:
        assert db.connection.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert latest_attribution(db, identifier) is None
        for table, original in before.items():
            assert [tuple(r) for r in db.connection.execute('SELECT * FROM ' + table)] == original
        assert not db.connection.execute('PRAGMA foreign_key_check').fetchall()


def test_reset_returns_to_source_basis_and_exports_are_consistent(tmp_path):
    from autonomo_taxes.books import write_accounting_books
    from autonomo_taxes.filing_package import write_filing_package, verify_filing_package
    from autonomo_taxes.aeat_books import build_aeat_book_projection
    from autonomo_taxes.tax_attribution import active_attribution_ids
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path)
        p = decision(db, identifier, irpf_date='2025-01-20')
        apply_attribution(db, p, actor='test-operator')
        out = write_accounting_books(db, tmp_path / 'books', period_key='2025-Q1')
        registers = json.loads(out.files['tax_registers'].read_text())
        assert registers['registers']['irpf']['blocked'] is False
        assert sum(Decimal(r['taxable_base_eur']) for r in registers['registers']['irpf']['rows']
                   if r['kind'] == 'income') == Decimal('935.00')
        assert sum(Decimal(r['deductible_irpf_eur']) for r in registers['registers']['irpf']['rows']) == Decimal('3.00')
        assert len(registers['bridge']) == 3
        m = write_filing_package(db, tmp_path / 'package', period_key='2025-Q1')
        assert verify_filing_package(db, m)['ok'] is True
        manifest = json.loads(m.read_text())
        assert manifest['unified_aeat_import_supported'] is False
        projection = build_aeat_book_projection(db, period_key='2025-Q1')
        assert projection['data_projection_ready'] is False
        assert projection['blockers'][0]['code'] == 'separate_tax_registers_required'
        assert projection['income_rows'] == []
        p = prepare_attribution(db, identifier)
        p['decision'] = {'irpf': None, 'iva': None, 'fx': []}
        p['reason'] = 'Withdraw attribution after source review'
        apply_attribution(db, p, actor='test-operator')
        assert active_attribution_ids(db) == set()
        assert len(load_tax_rows(db, 2025)) == 1
        assert calc130(load_tax_rows(db, 2025), 2025).values['01'] == Decimal('940.00')


def test_effective_forecast_and_accepted_history_do_not_duplicate_rows(tmp_path):
    from autonomo_taxes.current_quarter import build_current_quarter_dashboard
    from autonomo_taxes.operational_cli import _approved_forecast_rows
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path, day='2025-10-09', status='approved')
        p = decision(db, identifier, irpf_date='2025-09-22', iva_date='2025-11-10')
        p['decision']['fx'] = [dict(p['decision']['fx'][0], date='2025-09-29')]
        apply_attribution(db, p, actor='test-operator')
        details = _approved_forecast_rows(db, '2025-Q3')
        rows = load_tax_rows(db, 2025, include_approved_periods={'2025-Q3'})
        validation = {k: [] for k in ('blocking_issues', 'unresolved_obligations', 'review_documents',
            'review_transactions', 'target_derived_fx', 'invalid_amount_transactions', 'out_of_period_transactions')}
        args = dict(period_key='2025-Q3', as_of=date(2025, 10, 20), difficult_expenses_rate=Decimal('.05'),
                    previous_positive_casilla_07=Decimal(0), previous_negative_carry=Decimal(0),
                    previous_vat_compensation=Decimal(0), approved_current_rows=details,
                    obligations=[], period_validation=validation)
        draft = build_current_quarter_dashboard(actual_rows=[], projected_rows=rows, **args)
        assert draft['tax_arithmetic_preview']['posted_actual']['modelo130']['values']['01'] == 0
        assert draft['tax_arithmetic_preview']['projected_reviewed']['modelo130']['values']['01'] == Decimal('935.00')
        assert draft['approved_forecast_delta']['transaction_count'] == 0
        accepted = build_current_quarter_dashboard(actual_rows=rows, projected_rows=rows, **args)
        assert accepted['tax_arithmetic_preview']['projected_reviewed']['modelo130']['values']['01'] == Decimal('935.00')
        assert accepted['approved_forecast_delta']['transaction_count'] == 0


@pytest.mark.web
def test_http_auth_scope_cache_invalidation_and_failure_after_write(tmp_path, monkeypatch):
    from threading import Thread
    from http.client import HTTPConnection
    from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer, LocalWebConfig
    from autonomo_test_support.paths import REPO_ROOT
    database = tmp_path / 'db.sqlite'
    with initialize(database) as db:
        identifier = source(db, tmp_path)
        packet = decision(db, identifier)
    config = LocalWebConfig(project_root=REPO_ROOT, database=database, inbox_root=None,
                            archive_root=tmp_path, cache_root=tmp_path / 'cache',
                            static_root=REPO_ROOT / 'packages/ui/src/autonomo_taxes_ui/dist')
    app = LocalAccountingApp(config)
    app.refresh_dashboard('2025-Q1')
    assert app._load_cached_dashboard('2025-Q1')
    # A failed refresh must not turn the already committed review into an ambiguous failed write,
    # or allow the old amount to be presented as current.
    monkeypatch.setattr(app, 'refresh_dashboard', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('Synthetic refresh failure')))
    server = LocalAccountingServer(('127.0.0.1', 0), app)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    client = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
    try:
        client.request('GET', '/'); response = client.getresponse(); cookie = response.getheader('Set-Cookie').split(';', 1)[0]; response.read()
        headers = {'Cookie': cookie, 'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{server.server_port}'}
        def request(method, supplied_headers, data=None):
            client.request(method, '/api/tax-attributions/' + identifier,
                           body=json.dumps(data) if data is not None else None, headers=supplied_headers)
            response = client.getresponse(); return response.status, json.loads(response.read())
        assert request('GET', {})[0] in (400, 403)
        assert request('POST', {k:v for k,v in headers.items() if k != 'Origin'}, packet)[0] == 403
        assert request('POST', {**headers, 'Origin': 'http://127.0.0.1:9'}, packet)[0] == 403
        assert request('POST', {**headers, 'Content-Type': 'text/plain'}, packet)[0] in (400,415)
        code, saved = request('POST', headers, packet)
        assert code == 200 and saved['applied'] and saved['refresh_pending'] == ['2025-Q1']
        assert app._load_cached_dashboard('2025-Q1') == {}
        assert request('POST', headers, packet)[1]['current']['revision'] == 1
        changed = json.loads(json.dumps(packet)); changed['reason'] = 'Concurrent alternative decision'
        assert request('POST', headers, changed)[0] == 409
        assert request('GET', headers)[1]['current']['stale'] is False
        with open_database(database) as db:
            assert db.connection.execute('SELECT COUNT(*) FROM payments').fetchone()[0] == 0
            assert db.connection.execute('SELECT COUNT(*) FROM filing_snapshots').fetchone()[0] == 0
    finally:
        client.close(); server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_posting_preserves_attribution_but_document_revocation_invalidates_it(tmp_path):
    from autonomo_taxes.tax_attribution import attribution_view
    with initialize(tmp_path / 'db.sqlite') as db:
        identifier = source(db, tmp_path, status='approved')
        packet = decision(db, identifier, irpf_date='2025-01-20')
        apply_attribution(db, packet, actor='test-operator')
        db.connection.execute("UPDATE transactions SET lifecycle_status='posted', row_version=row_version+1 WHERE transaction_id=?", (identifier,))
        db.connection.execute("UPDATE documents SET lifecycle_status='posted', row_version=row_version+1")
        assert attribution_view(db, identifier)['stale'] is False
        assert calc130(load_tax_rows(db, 2025), 2025).values['01'] == Decimal('935.00')
        db.connection.execute("UPDATE documents SET lifecycle_status='needs_review', row_version=row_version+1")
        assert attribution_view(db, identifier)['stale'] is True
        with pytest.raises(CalculationBlocked, match='Source facts changed'):
            calc130(load_tax_rows(db, 2025), 2025)
