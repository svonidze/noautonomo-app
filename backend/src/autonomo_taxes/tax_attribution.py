"""Reviewed tax dates and amounts without rewriting source transactions."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Iterable
from uuid import uuid4

from .ledger_db import LedgerDB
from .tax_engine import CalculationBlocked, TaxBasis, TaxRow


class TaxAttributionError(ValueError):
    def __init__(self, message: str, *, conflict: bool = False):
        super().__init__(message)
        self.status = 409 if conflict else 400
        self.code = "tax_attribution_stale" if conflict else "tax_attribution_invalid"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise TaxAttributionError(f"{label} must be nonempty text of at most 4000 characters")
    return value.strip()


def _date(value: Any) -> date:
    if not isinstance(value, str):
        raise TaxAttributionError("Attribution date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise TaxAttributionError("Attribution date must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise TaxAttributionError("Attribution date must be YYYY-MM-DD")
    return parsed


def _minor(value: Any, label: str, *, signed: bool = False) -> int:
    if type(value) is not int or abs(value) > 10**14 or (not signed and value < 0):
        raise TaxAttributionError(f"{label} must be {'signed ' if signed else 'nonnegative '}integer EUR cents")
    return value


def _shape(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise TaxAttributionError(f"Invalid {label} fields; expected {', '.join(sorted(keys))}")
    return value


def source_state(db: LedgerDB, transaction_id: str) -> dict[str, Any]:
    row = db.connection.execute("SELECT * FROM transactions WHERE transaction_id=?", (transaction_id,)).fetchone()
    if row is None:
        raise TaxAttributionError("Transaction not found")
    tx = dict(row)
    doc = db.connection.execute("SELECT * FROM documents WHERE document_id=?", (tx['document_id'],)).fetchone()
    treatments = [dict(r) for r in db.connection.execute(
        "SELECT * FROM tax_treatments WHERE transaction_id=? ORDER BY treatment_id", (transaction_id,)
    )]
    party = db.connection.execute(
        "SELECT country_code, tax_id, vat_id FROM counterparties WHERE counterparty_id=?", (tx['counterparty_id'],)
    ).fetchone()
    # Lifecycle and audit timestamps change on posting/snapshotting without changing tax facts.
    facts = {k: v for k, v in tx.items() if k not in {
        'lifecycle_status', 'row_version', 'created_at', 'updated_at', 'included_snapshot_id', 'description',
    }}
    source = {
        'transaction': facts,
        'transaction_eligibility': ('reviewed' if tx['lifecycle_status'] in
                                    {'approved', 'posted', 'included_in_snapshot'} else tx['lifecycle_status']),
        'document': ({k: doc[k] for k in (
            'document_id', 'source_hash', 'document_number', 'issued_on', 'currency', 'total_minor', 'counterparty_id',
        )} if doc else None),
        'treatments': [{k: v for k, v in t.items() if k not in {
            'row_version', 'created_at', 'updated_at', 'notes', 'source_hash',
        }} for t in treatments],
        'counterparty': dict(party) if party else None,
        'document_eligibility': ('reviewed' if doc and doc['lifecycle_status'] in
                                 {'approved', 'posted', 'included_in_snapshot'} else doc['lifecycle_status'] if doc else None),
    }
    return {'transaction': tx, 'document': dict(doc) if doc else None,
            'treatments': treatments, 'source_snapshot_hash': _hash(source)}


def latest_attribution(db: LedgerDB, transaction_id: str) -> dict[str, Any] | None:
    row = db.connection.execute(
        "SELECT * FROM transaction_tax_attributions WHERE transaction_id=? ORDER BY revision DESC LIMIT 1",
        (transaction_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    decision = json.loads(result.pop('decision_json'))
    decision['fx'] = [
        {'event_key': r['event_key'], 'date': r['recognized_on'],
         'amount_minor': r['amount_minor'], 'evidence_ref': r['evidence_ref']}
        for r in db.connection.execute(
            "SELECT * FROM tax_attribution_fx_events WHERE attribution_id=? ORDER BY event_key",
            (result['attribution_id'],),
        )
    ]
    result['decision'] = decision
    result['historical_correction'] = bool(result['historical_correction'])
    return result


def attribution_view(db: LedgerDB, transaction_id: str) -> dict[str, Any] | None:
    current = latest_attribution(db, transaction_id)
    if current is not None:
        current['stale'] = current['source_snapshot_hash'] != source_state(db, transaction_id)['source_snapshot_hash']
    return current


def prepare_attribution(db: LedgerDB, transaction_id: str) -> dict[str, Any]:
    source = source_state(db, transaction_id)
    current = latest_attribution(db, transaction_id)
    return {
        'transaction_id': transaction_id,
        'expected_revision': current['revision'] if current else 0,
        'source_snapshot_hash': source['source_snapshot_hash'],
        'decision': current['decision'] if current else {'irpf': None, 'iva': None, 'fx': []},
        'evidence_ref': current['evidence_ref'] if current else '',
        'reason': current['reason'] if current else '',
        'historical_correction': current['historical_correction'] if current else False,
    }


def attribution_state_hash(db: LedgerDB) -> str | None:
    latest = db.connection.execute("""SELECT a.transaction_id, a.revision, a.source_snapshot_hash
        FROM transaction_tax_attributions a WHERE a.revision = (
            SELECT MAX(b.revision) FROM transaction_tax_attributions b WHERE b.transaction_id=a.transaction_id)
        ORDER BY a.transaction_id""").fetchall()
    if not latest:
        return None
    values = []
    for row in latest:
        source = source_state(db, row['transaction_id'])
        values.append([row['transaction_id'], row['revision'], row['source_snapshot_hash'],
                       source['source_snapshot_hash'], source['transaction']['lifecycle_status']])
    return _hash(values)


def active_attribution_ids(db: LedgerDB) -> set[str]:
    return {r[0] for r in db.connection.execute("""
        SELECT a.transaction_id FROM transaction_tax_attributions a
        WHERE a.revision = (SELECT MAX(b.revision) FROM transaction_tax_attributions b
                            WHERE b.transaction_id=a.transaction_id)
          AND (json_extract(a.decision_json,'$.irpf') IS NOT NULL
            OR json_extract(a.decision_json,'$.iva') IS NOT NULL
            OR EXISTS (SELECT 1 FROM tax_attribution_fx_events fx WHERE fx.attribution_id=a.attribution_id))
    """)}


def _validate_decision(decision: Any, source: dict[str, Any]) -> dict[str, Any]:
    _shape(decision, {'irpf', 'iva', 'fx'}, 'decision')
    kind = source['transaction']['entry_type']
    if kind not in {'income', 'expense'}:
        raise TaxAttributionError("Only income and expense transactions support tax attributions")
    if source['transaction']['amount_minor'] < 0:
        raise TaxAttributionError("Credit notes require their existing correction workflow")
    irpf, iva = decision['irpf'], decision['iva']
    if (irpf is not None or decision['fx']) and not any(t['include_modelo130'] for t in source['treatments']):
        raise TaxAttributionError("The source treatment excludes IRPF; review its classification first")
    if iva is not None and not any(t['include_modelo303'] for t in source['treatments']):
        raise TaxAttributionError("The source treatment excludes IVA; review its classification first")
    if irpf is not None:
        _shape(irpf, {'date', 'income_minor', 'deductible_minor', 'withholding_minor', 'evidence_ref'}, 'IRPF')
        _date(irpf['date']); _text(irpf['evidence_ref'], 'IRPF evidence')
        for key in ('income_minor', 'deductible_minor', 'withholding_minor'):
            _minor(irpf[key], key)
        if (kind == 'income' and irpf['deductible_minor']) or (
            kind == 'expense' and (irpf['income_minor'] or irpf['withholding_minor'])
        ):
            raise TaxAttributionError("IRPF income/expense fields do not match the source transaction")
        if kind == 'income' and irpf['withholding_minor'] > irpf['income_minor']:
            raise TaxAttributionError("Withholding exceeds IRPF income")
    if iva is not None:
        _shape(iva, {'date', 'base_minor', 'vat_minor', 'deductible_minor', 'pending_reason', 'evidence_ref'}, 'IVA')
        _date(iva['date']); _text(iva['evidence_ref'], 'IVA evidence')
        values = [iva[k] for k in ('base_minor', 'vat_minor', 'deductible_minor')]
        if iva['pending_reason'] is not None:
            _text(iva['pending_reason'], 'Pending IVA reason')
            if any(value is not None for value in values):
                raise TaxAttributionError("Pending IVA must not contain provisional amounts")
        else:
            for key in ('base_minor', 'vat_minor', 'deductible_minor'):
                _minor(iva[key], key)
            if iva['deductible_minor'] > iva['vat_minor'] or (kind == 'income' and iva['deductible_minor']):
                raise TaxAttributionError("Invalid deductible IVA quota")
            if any(t['tax_code'] in {'outside_scope', 'not_subject_place_of_supply', 'export', 'eu_service_income', 'eu_goods_income'}
                   for t in source['treatments']) and iva['vat_minor']:
                raise TaxAttributionError("The source classification has no Spanish output VAT")
    if not isinstance(decision['fx'], list) or len(decision['fx']) > 100:
        raise TaxAttributionError("FX events must be a list of at most 100 reviewed settlements")
    seen = set()
    for event in decision['fx']:
        _shape(event, {'event_key', 'date', 'amount_minor', 'evidence_ref'}, 'FX event')
        key = _text(event['event_key'], 'FX event key')
        if key != event['event_key'] or key in seen or kind != 'income':
            raise TaxAttributionError("FX events require unique keys and an income parent")
        seen.add(key); _date(event['date']); _text(event['evidence_ref'], 'FX evidence')
        if not _minor(event['amount_minor'], 'FX amount', signed=True):
            raise TaxAttributionError("Zero FX is not a recognition event")
    return json.loads(_canonical({**decision, 'fx': sorted(decision['fx'], key=lambda e: e['event_key'])}))


def _affected_dates(source: dict[str, Any], decision: dict[str, Any]) -> set[str]:
    dates = {source['transaction']['transaction_date']}
    for basis in ('irpf', 'iva'):
        if decision[basis]:
            dates.add(decision[basis]['date'])
    dates.update(event['date'] for event in decision['fx'])
    return dates


def apply_attribution(db: LedgerDB, request: dict[str, Any], *, actor: str) -> dict[str, Any]:
    _shape(request, {'transaction_id', 'expected_revision', 'source_snapshot_hash', 'decision',
                     'evidence_ref', 'reason', 'historical_correction'}, 'attribution request')
    transaction_id = _text(request['transaction_id'], 'Transaction ID')
    revision = _minor(request['expected_revision'], 'Expected revision')
    evidence = _text(request['evidence_ref'], 'Evidence reference')
    reason = _text(request['reason'], 'Review reason'); actor = _text(actor, 'Actor')
    if type(request['historical_correction']) is not bool:
        raise TaxAttributionError("Historical correction acknowledgement must be boolean")
    with db.transaction():
        source = source_state(db, transaction_id)
        if source['source_snapshot_hash'] != request['source_snapshot_hash']:
            raise TaxAttributionError("Source facts changed; prepare and review again", conflict=True)
        if source['transaction']['lifecycle_status'] not in {'approved', 'posted', 'included_in_snapshot'}:
            raise TaxAttributionError("Review the source transaction before its tax attribution")
        if not source['document'] or source['document']['lifecycle_status'] not in {'approved', 'posted', 'included_in_snapshot'}:
            raise TaxAttributionError("A reviewed source document is required")
        if not source['treatments'] or any(t['tax_code'] in (None, '', 'unknown') for t in source['treatments']):
            raise TaxAttributionError("A reviewed tax treatment is required")
        from .review_packet import _verify_archived_document
        _verify_archived_document(db, {'document': source['document']})
        decision = _validate_decision(request['decision'], source)
        previous = latest_attribution(db, transaction_id)
        dates = _affected_dates(source, decision)
        if previous:
            dates |= _affected_dates(source, previous['decision'])
        for raw_date in dates:
            day = _date(raw_date)
            keys = (str(day.year), f'{day.year}-Q{(day.month - 1) // 3 + 1}')
            filed = db.connection.execute("""SELECT 1 FROM periods p WHERE p.period_key IN (?, ?)
                AND (p.status IN ('closed','amended') OR EXISTS (
                    SELECT 1 FROM obligations o WHERE o.period_id=p.period_id AND o.filing_status='filed')
                    OR EXISTS (SELECT 1 FROM filing_snapshots fs WHERE fs.period_id=p.period_id)) LIMIT 1""", keys).fetchone()
            if filed and not request['historical_correction']:
                raise TaxAttributionError("Closed/filed periods require explicit corrected-view acknowledgement")
        if previous and all((
            previous['decision'] == decision, previous['source_snapshot_hash'] == source['source_snapshot_hash'],
            previous['reason'] == reason, previous['evidence_ref'] == evidence,
            previous['historical_correction'] == request['historical_correction'],
        )):
            return previous
        if revision != (previous['revision'] if previous else 0):
            raise TaxAttributionError("Attribution changed; prepare and review again", conflict=True)
        identifier = str(uuid4())
        db.connection.execute("""INSERT INTO transaction_tax_attributions VALUES(?,?,?,?,?,?,?,?,?,?)""", (
            identifier, transaction_id, revision + 1, source['source_snapshot_hash'],
            _canonical({key: decision[key] for key in ('irpf', 'iva')}), evidence, reason, actor,
            int(request['historical_correction']), datetime.now(timezone.utc).isoformat(),
        ))
        db.connection.executemany("INSERT INTO tax_attribution_fx_events VALUES(?,?,?,?,?)", [
            (identifier, e['event_key'], e['date'], e['amount_minor'], e['evidence_ref']) for e in decision['fx']
        ])
    return latest_attribution(db, transaction_id)


def _basis(payload: dict[str, Any] | None, basis: str, row: TaxRow, error: str | None) -> TaxBasis | None:
    if payload is None:
        return None
    day = _date(payload['date'])
    if basis == 'irpf':
        income = Decimal(payload['income_minor']) / 100
        return TaxBasis(day, income if row.kind == 'income' else row.amount_eur,
                        income, deductible_irpf_eur=Decimal(payload['deductible_minor']) / 100,
                        withholding_eur=Decimal(payload['withholding_minor']) / 100, error=error,
                        evidence_ref=payload['evidence_ref'])
    if payload['pending_reason'] is not None:
        return TaxBasis(day, error=error or 'IVA amounts pending: ' + payload['pending_reason'],
                        evidence_ref=payload['evidence_ref'])
    base = Decimal(payload['base_minor']) / 100
    return TaxBasis(day, base, base, vat_eur=Decimal(payload['vat_minor']) / 100,
                    deductible_vat_eur=Decimal(payload['deductible_minor']) / 100, error=error,
                    evidence_ref=payload['evidence_ref'])


def attach_attributions(db: LedgerDB, rows: Iterable[TaxRow]) -> list[TaxRow]:
    result = []
    for row in rows:
        current = latest_attribution(db, row.transaction_id)
        if current is None or current['decision'] == {'irpf': None, 'iva': None, 'fx': []}:
            result.append(row); continue
        source = source_state(db, row.transaction_id)
        try:
            decision = _validate_decision(current['decision'], source)
        except ValueError as exc:
            raise CalculationBlocked(f"Invalid stored tax attribution for {row.transaction_id}: {exc}") from exc
        error = ('Source facts changed since tax attribution review' if
                 current['source_snapshot_hash'] != source['source_snapshot_hash'] else None)
        result.append(replace(row, attribution_id=current['attribution_id'],
            attribution_evidence_ref=current['evidence_ref'], attribution_reason=current['reason'],
            irpf_basis=_basis(decision['irpf'], 'irpf', row, error),
            iva_basis=_basis(decision['iva'], 'iva', row, error)))
        for event in decision['fx']:
            amount = Decimal(event['amount_minor']) / 100
            day = _date(event['date'])
            is_gain = amount > 0
            basis = TaxBasis(day, abs(amount), amount if is_gain else Decimal(0),
                             deductible_irpf_eur=-amount if not is_gain else Decimal(0), error=error,
                             evidence_ref=event['evidence_ref'])
            result.append(TaxRow(
                transaction_id=f"fx:{row.transaction_id}:{event['event_key']}", source_transaction_id=row.transaction_id,
                tax_date=day, kind='income' if is_gain else 'expense', amount_eur=abs(amount),
                taxable_base_eur=basis.taxable_base_eur, deductible_irpf_eur=basis.deductible_irpf_eur,
                tax_code='outside_scope' if is_gain else 'not_subject', include_modelo130=row.include_modelo130,
                counterparty_id=row.counterparty_id, counterparty_name=row.counterparty_name,
                country_code=row.country_code, vat_id=row.vat_id, vat_investment_good=False,
                component='fx_gain' if is_gain else 'fx_loss', attribution_id=current['attribution_id'], irpf_basis=basis,
                document_id=row.document_id, document_number=row.document_number,
                issued_on=row.issued_on, document_source_hash=row.document_source_hash,
                attribution_evidence_ref=current['evidence_ref'], attribution_reason=current['reason'],
                fx_evidence_ref=event['evidence_ref'],
            ))
    return result


def attributed_dates(db: LedgerDB, transaction_id: str, original_date: str) -> set[date]:
    dates = {original_date}
    current = latest_attribution(db, transaction_id)
    if current:
        for basis in ('irpf', 'iva'):
            if current['decision'][basis]:
                dates.add(current['decision'][basis]['date'])
        dates.update(e['date'] for e in current['decision']['fx'])
    return set(map(_date, dates))


def attributed_periods(db: LedgerDB, transaction_id: str, original_date: str) -> set[str]:
    return {f'{d.year}-Q{(d.month - 1) // 3 + 1}' for d in attributed_dates(db, transaction_id, original_date)}


def register_projection(rows: Iterable[TaxRow], *, year: int, through: date,
                        iva_starts_on: date | None = None) -> dict[str, Any]:
    from .tax_engine import project_tax_rows, calculate_modelo303_rows
    rows = list(rows)
    result: dict[str, Any] = {'date_basis': 'separate_reviewed_irpf_iva', 'registers': {}, 'bridge': []}
    selected_ids: set[str] = set()
    parent_ids: set[str] = set()
    for basis in ('irpf', 'iva'):
        start = iva_starts_on if basis == 'iva' and iva_starts_on else date(year, 1, 1)
        scope = {'starts_on': start, 'through': through}
        projected = []
        blocked_rows = []
        for row in rows:
            try:
                resolved = project_tax_rows([row], basis, start, through)
                if not resolved:
                    continue
                if basis == 'iva':
                    # Use the calculator's complete classification/rate guard, not an exporter copy.
                    calculate_modelo303_rows(resolved, year=year,
                                             quarter=(resolved[0].tax_date.month - 1) // 3 + 1)
                projected.extend(resolved)
            except CalculationBlocked as exc:
                day = row.effective_date(basis)
                blocked_rows.append({'transaction_id': row.transaction_id,
                    'source_transaction_id': row.parent_id, 'tax_date': day,
                    'period': f'{day.year}-Q{(day.month - 1) // 3 + 1}', 'reason': str(exc)})
            selected_ids.add(row.transaction_id)
            parent_ids.add(row.parent_id)
        projected.sort(key=lambda row: (row.tax_date, row.transaction_id))
        result['registers'][basis] = {**scope, 'blocked': bool(blocked_rows),
            'rows': [] if blocked_rows else [asdict(row) for row in projected]}
        if blocked_rows:
            result['registers'][basis].update(reason=blocked_rows[0]['reason'], blocked_rows=blocked_rows)
    result['selected_row_ids'] = sorted(selected_ids)
    result['source_rows'] = [asdict(row) for row in rows
                             if row.component == 'transaction' and row.transaction_id in parent_ids]
    for row in rows:
        if row.attribution_id:
            result['bridge'].append({
                'transaction_id': row.transaction_id, 'source_transaction_id': row.parent_id,
                'component': row.component, 'attribution_id': row.attribution_id,
                'evidence_ref': row.attribution_evidence_ref, 'reason': row.attribution_reason,
                'fx_evidence_ref': row.fx_evidence_ref,
                'original_date': row.tax_date, 'original_amount_eur': row.amount_eur,
                'original_taxable_base_eur': row.taxable_base_eur,
                'irpf': asdict(row.irpf_basis) if row.irpf_basis else None,
                'iva': asdict(row.iva_basis) if row.iva_basis else None,
            })
    return result
