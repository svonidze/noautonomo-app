from autonomo_taxes.depreciation import book_year_summary, accumulated_at
from autonomo_taxes.ledger_db import open as open_db
from test_asset_lifecycle import imported_asset
from test_aeat_books import _historical_asset_charge


def test_historical_charges_ignore_future_unposted_and_derived_schedules(tmp_path):
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture['database']) as db:
        asset = dict(db.connection.execute('SELECT * FROM assets WHERE asset_id=?', (asset_id,)).fetchone())
        db.add_amortization_entry(asset_id=asset_id, period_key='2025', amount_minor=2600,
            entry_kind='annual_evidence', include_in_books=False, source_hash='synthetic:annual')
        for day, status in [('2025-03-31','posted'), ('2025-06-30','approved'),
                            ('2025-09-30','included_in_snapshot'), ('2025-12-31','posted')]:
            _historical_asset_charge(db, asset, day, 650, status=status)
        db.add_amortization_entry(asset_id=asset_id, period_key='2025-Q1', amount_minor=999,
            include_in_books=False, source_hash='synthetic:derived')
        before = '\n'.join(db.connection.iterdump())
        summary = book_year_summary(db.connection, asset_id, 2025, through_date='2025-09-30')
        assert summary['prior_minor'] == 2493
        assert summary['current_minor'] == 1300
        assert summary['pending'] == []
        assert summary['annual_control_minor'] == 2600
        assert accumulated_at(db.connection, asset_id, '2025-09-30') == 3793
        assert 'historical_annual_recognition_mismatch' in book_year_summary(db.connection, asset_id, 2025)['pending']
        assert '\n'.join(db.connection.iterdump()) == before


def test_direct_acquisition_g03_charge_is_not_lost_because_invoice_has_vat(tmp_path):
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture['database']) as db:
        asset = db.connection.execute('SELECT * FROM assets WHERE asset_id=?', (asset_id,)).fetchone()
        db.connection.execute("UPDATE documents SET document_type='gastos_book' WHERE document_id=?", (asset['document_id'],))
        db.connection.execute("""UPDATE tax_treatments SET aeat_expense_concept='G03',
            deductible_irpf_minor=1000,include_modelo130=1 WHERE transaction_id=?""", (asset['acquisition_transaction_id'],))
        row = db.connection.execute('SELECT vat_minor FROM tax_treatments WHERE transaction_id=?', (asset['acquisition_transaction_id'],)).fetchone()
        assert row[0] > 0
        summary = book_year_summary(db.connection, asset_id, 2024, through_date='2024-03-31')
        assert summary['current_minor'] == 1000
        assert summary['pending'] == []


def test_annual_control_does_not_prove_posted_recognition(tmp_path):
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture['database']) as db:
        summary = book_year_summary(db.connection, asset_id, 2024, through_date='2024-09-30')
        assert summary['current_minor'] == 0
        assert 'historical_depreciation_recognition_missing' in summary['pending']


def test_missing_current_or_intermediate_opening_year_stays_pending(tmp_path):
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture['database']) as db:
        summary = book_year_summary(db.connection, asset_id, 2025, through_date='2025-03-31')
        assert 'historical_current_year_evidence_missing' in summary['pending']
        asset = dict(db.connection.execute('SELECT * FROM assets WHERE asset_id=?', (asset_id,)).fetchone())
        db.add_amortization_entry(asset_id=asset_id, period_key='2026', amount_minor=2600,
            entry_kind='annual_evidence', include_in_books=False, source_hash='synthetic:annual')
        _historical_asset_charge(db, asset, '2026-03-31', 650)
        summary = book_year_summary(db.connection, asset_id, 2026, through_date='2026-03-31')
        assert 'historical_opening_year_missing:2025' in summary['pending']


def test_conflicting_non_g03_treatment_cannot_be_hidden_by_filter(tmp_path):
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture['database']) as db:
        asset = dict(db.connection.execute('SELECT * FROM assets WHERE asset_id=?', (asset_id,)).fetchone())
        tx = _historical_asset_charge(db, asset, '2025-03-31', 650)
        db.add_detailed_tax_treatment(transaction_id=tx['transaction_id'], treatment_type='conflicting',
            tax_code='domestic_input', aeat_expense_concept='G01', deductible_irpf_minor=700)
        summary = book_year_summary(db.connection, asset_id, 2025, through_date='2025-03-31')
        assert any('historical_depreciation_conflict:' in issue for issue in summary['pending'])


def test_ambiguous_charge_cannot_hide_behind_an_unambiguous_charge(tmp_path):
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture['database']) as db:
        asset = dict(db.connection.execute('SELECT * FROM assets WHERE asset_id=?', (asset_id,)).fetchone())
        db.add_amortization_entry(asset_id=asset_id, period_key='2025', amount_minor=2600,
            entry_kind='annual_evidence', include_in_books=False, source_hash='synthetic:annual')
        direct = _historical_asset_charge(db, asset, '2025-03-31', 650)
        db.connection.execute('UPDATE assets SET acquisition_transaction_id=? WHERE asset_id=?', (direct['transaction_id'], asset_id))
        db.add_asset(asset_code='AMBIGUOUS', document_id=asset['document_id'],
            placed_in_service_on='2024-01-02', cost_minor=10000, amortizable_base_minor=10000,
            currency='EUR', depreciation_method='linear', source_hash='synthetic:ambiguous')
        _historical_asset_charge(db, asset, '2025-06-30', 650)
        summary = book_year_summary(db.connection, asset_id, 2025, through_date='2025-09-30')
        assert summary['current_minor'] == 650
        assert any('historical_depreciation_invalid:' in issue for issue in summary['pending'])


def test_cross_year_irpf_attribution_is_not_silently_omitted(tmp_path):
    from autonomo_taxes.tax_attribution import prepare_attribution, apply_attribution
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture['database']) as db:
        asset = dict(db.connection.execute('SELECT * FROM assets WHERE asset_id=?', (asset_id,)).fetchone())
        tx = _historical_asset_charge(db, asset, '2024-12-31', 650)
        packet = prepare_attribution(db, tx['transaction_id'])
        packet.update(reason='Synthetic reviewed cross-year charge', evidence_ref='synthetic:review',
            decision={'irpf': {'date':'2025-03-31','income_minor':0,'deductible_minor':650,
                'withholding_minor':0,'evidence_ref':'synthetic:date'}, 'iva':None, 'fx':[]})
        apply_attribution(db, packet, actor='test')
        summary = book_year_summary(db.connection, asset_id, 2025, through_date='2025-03-31')
        assert any('historical_depreciation_attribution_requires_review:' in issue for issue in summary['pending'])


def test_aeat_asset_method_must_match_reviewed_native_plan(tmp_path):
    from autonomo_test_support.expense_workflow import setup_draft
    from test_expense_workflow import commit
    from autonomo_taxes.aeat_books import build_aeat_book_projection
    fixture, draft = setup_draft(tmp_path, method='immediate', transaction_date='2024-01-02')
    with open_db(fixture['database']) as db:
        result = commit(db, fixture, draft, tmp_path)
        db.connection.execute("UPDATE counterparties SET tax_id='TEST-TAX-ID-001'")
        db.connection.execute("UPDATE assets SET aeat_amortization_method='02' WHERE asset_id=?", (result['asset_id'],))
        projection = build_aeat_book_projection(db, period_key='2024-Q1')
        assert any('disagrees with the reviewed depreciation plan' in b['message'] for b in projection['blockers'])
        assert not projection['asset_rows']


def test_future_continuation_method_does_not_reclassify_historical_book(tmp_path):
    from test_asset_lifecycle import opening
    from autonomo_taxes.aeat_books import build_aeat_book_projection
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture['database']) as db:
        db.connection.execute("UPDATE counterparties SET tax_id='TEST-TAX-ID-001'")
        db.connection.execute("UPDATE assets SET aeat_amortization_method='02' WHERE asset_id=?", (asset_id,))
        asset = dict(db.connection.execute('SELECT * FROM assets WHERE asset_id=?', (asset_id,)).fetchone())
        _historical_asset_charge(db, asset, '2024-12-31', 2493)
        before = build_aeat_book_projection(db, period_key='2024-Q4')
        assert before['data_projection_ready'], before['blockers']
        opening(db, asset_id)
        after = build_aeat_book_projection(db, period_key='2024-Q4')
        assert after['data_projection_ready'], after['blockers']
        assert after['asset_rows'] == before['asset_rows']


def test_mixed_legacy_and_linked_recognition_cannot_be_silently_omitted(tmp_path):
    fixture, asset_id = imported_asset(tmp_path)
    with open_db(fixture['database']) as db:
        asset = dict(db.connection.execute('SELECT * FROM assets WHERE asset_id=?', (asset_id,)).fetchone())
        db.add_amortization_entry(asset_id=asset_id, period_key='2025', amount_minor=2600,
            entry_kind='annual_evidence', include_in_books=False, source_hash='synthetic:annual')
        _historical_asset_charge(db, asset, '2025-03-31', 650)
        linked = _historical_asset_charge(db, asset, '2025-06-30', 650)
        entry = db.add_amortization_entry(asset_id=asset_id, period_key='2025-Q2', amount_minor=650,
            include_in_books=True, source_hash='synthetic:linked')
        db.connection.execute('UPDATE amortization_entries SET recognition_transaction_id=?,recognition_on=? WHERE amortization_entry_id=?',
            (linked['transaction_id'], '2025-06-30', entry['amortization_entry_id']))
        summary = book_year_summary(db.connection, asset_id, 2025, through_date='2025-09-30')
        assert summary['current_minor'] == 650
        assert any('historical_linked_recognition_requires_review:' in issue for issue in summary['pending'])
