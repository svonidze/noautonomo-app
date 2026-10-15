"""Separate AEAT books: cumulative scope, explicit evidence and frozen numbering."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import re
from pathlib import Path
import zipfile
from typing import Any, Mapping

from . import aeat_books as common
from .aeat_workbook import (
    ASSET_COLUMNS, INCOME_COLUMNS, EXPENSE_COLUMNS, MAX_AEAT_WORKBOOK_BYTES,
    OFFICIAL_TEMPLATE_SHA256, inspect_aeat_template, _workbook_sheets, _shared_strings, _sheet_rows,
)
from .tax_attribution import register_projection
from .tax_row_loader import load_tax_rows

EXCLUSIVE = {
    'income': {'irpf': ('G','H','AE','AF'), 'iva': ('R','S','T','AG')},
    'expense': {'irpf': ('G','H','AK','AL'), 'iva': ('M','T','U','V','W','X','Y','AM')},
    'asset': {'irpf': ('G','H','J','K','L','M','N','O','P','Q','U','V','AI'),
              'iva': ('AA','AB','AC','AD','AE','AF','AG','AM')},
}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def read_registration_act(path: Path, *, expected_sha256: str) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Registration act hash mismatch')
    act = json.loads(raw)
    if act.get('schema_version') != 1 or act.get('status') != 'approved' or not str(act.get('approved_by','')).strip():
        raise ValueError('Registration act requires explicit approval and an author')
    if act.get('origin') not in {'reconstructed','original_evidenced'}:
        raise ValueError('Registration act must identify the origin of its numbers')
    assigned_on = date.fromisoformat(act['assigned_on'])
    keys, numbers, groups, scope = set(), set(), {}, {}
    for entry in act['entries']:
        if entry['entry_key'] in keys or entry['number'] in numbers:
            raise ValueError('Registration act contains duplicate keys or numbers')
        if not entry.get('source_hash') or not 0 < len(entry['number']) <= 20:
            raise ValueError('Registration number or source evidence is invalid')
        if type(entry['ordinal']) is not int or entry['ordinal'] < 1 or date.fromisoformat(entry['source_date']) > assigned_on:
            raise ValueError('Registration ordinal or assignment date is invalid')
        keys.add(entry['entry_key']); numbers.add(entry['number'])
        groups.setdefault(entry['series'], []).append(entry['ordinal'])
        group_scope = (entry['book_year'], entry['kind'])
        if scope.setdefault(entry['series'], group_scope) != group_scope:
            raise ValueError('SF and invoice numbers require independent yearly series')
    if any(sorted(values) != list(range(1, len(values)+1)) for values in groups.values()):
        raise ValueError('Registration series must be consecutive')
    return act | {'file_sha256': expected_sha256}


def sf_requirement(row: Mapping[str, Any], *, acquisition: bool = False) -> dict[str, Any]:
    proof = {'row_id': row['transaction_id'], 'component': row['component'],
        'source_document_hash': row.get('document_source_hash'), 'date': str(row['tax_date']),
        'amount': str(row['taxable_base_eur'] if row['kind'] == 'income' else row['deductible_irpf_eur']),
        'fx_evidence': row.get('fx_evidence_ref'), 'attribution_id': row.get('attribution_id')}
    return {'entry_key': 'sf:'+row['transaction_id']+(':depreciation' if acquisition else ''),
        'source_hash': _hash(proof), 'kind': 'sf_income' if row['kind']=='income' else 'sf_expense',
        'book_year': row['tax_date'].year, 'source_date': str(row['tax_date']),
        'source_proof': proof}


def _minor(value: Any) -> int:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError('Non-finite book amount')
    return int((number*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def build_separate_projection(database, *, period_key: str, tax: str,
                              registrations: Mapping[str, Any] | None = None,
                              operation_dates: Mapping[str, Any] | None = None,
                              allow_authoritative_history: bool = False) -> dict[str, Any]:
    if tax not in {'iva', 'irpf'}:
        raise ValueError('Choose iva or irpf')
    year, quarter = common._parse_quarter(period_key)
    end = common._quarter_end(year, quarter)
    profile = common._taxpayer_profile(database, None)
    activities = database.list_business_activities(taxpayer_profile_id=profile['taxpayer_profile_id'])
    effective = register_projection(load_tax_rows(database, year,
        allow_authoritative_history=allow_authoritative_history), year=year, through=end,
        iva_starts_on=date(year, 1, 1), for_books=True)
    register = effective['registers'][tax]
    result = {'tax': tax, 'period': period_key, 'starts_on': f'{year}-01-01', 'through': str(end),
        'taxpayer': profile, 'income_rows': [], 'expense_rows': [], 'asset_rows': [],
        'blockers': [], 'registration_requirements': [], 'lineage': [], 'ready': False,
        'registration_act_sha256': (registrations or {}).get('file_sha256'),
        'source_row_ids': [row['transaction_id'] for row in register['rows']],
        'source_completeness_verified': False, 'annual_complete': False}
    if register['blocked']:
        result['blockers'] = register.get('blocked_rows', [{'reason': register['reason']}])
        return result
    if registrations and (registrations.get('status') != 'approved' or not registrations.get('approved_by')):
        raise ValueError('Unapproved numbering cannot be used in an official book')
    assigned = {entry['entry_key']: entry for entry in (registrations or {}).get('entries', [])}
    raw_tax_rows = database.list_tax_rows(year=year)
    source_years = {int(row['transaction_date'][:4]) for row in raw_tax_rows}
    sources = {row['transaction_id']: row for source_year in source_years
        for row in common._transaction_rows(database, year=source_year, through_date=date(source_year,12,31))}
    assets = [dict(row) for row in database.connection.execute('SELECT * FROM assets')]
    acquisitions = {row['acquisition_transaction_id'] for row in assets if row['acquisition_transaction_id']}
    retained_receipts = {'invoice:'+row['document_id']:dict(row) for row in
        database.connection.execute('SELECT * FROM receipt_registrations')}
    used_numbers = {row['receipt_number']:key for key,row in retained_receipts.items()}

    def blocker(key, reason):
        result['blockers'].append({'reference': key, 'reason': reason})

    def number(requirement, existing=''):
        key = requirement['entry_key']
        if not any(x['entry_key']==key for x in result['registration_requirements']):
            result['registration_requirements'].append(requirement)
        entry = assigned.get(key)
        retained = retained_receipts.get(key)
        if retained:
            if retained['document_source_hash'] != requirement['source_hash']:
                blocker(key, 'Retained receipt source evidence changed; renumbering is prohibited'); return ''
            existing = retained['receipt_number']
        if entry and any(entry.get(field) != requirement[field] for field in ('kind','book_year','source_date')):
            blocker(key, 'Numbering scope differs from the source record'); return ''
        if entry and entry['source_hash'] != requirement['source_hash']:
            blocker(key, 'Numbering source evidence changed'); return ''
        if entry and existing and entry['number'] != existing:
            blocker(key, 'Existing and supplied receipt numbers disagree'); return ''
        value = existing or (entry['number'] if entry else '')
        if not value:
            blocker(key, 'Evidenced registration number required')
        elif used_numbers.setdefault(value, key) != key:
            blocker(key, 'Registration number is already assigned to another source record'); return ''
        return value

    def invoice_requirement(document_id):
        document = database.connection.execute('SELECT source_hash, issued_on FROM documents WHERE document_id=?', (document_id,)).fetchone()
        if document is None or not document['source_hash'] or not document['issued_on']:
            raise ValueError('Invoice registration lacks dated source evidence')
        return {'entry_key':'invoice:'+document_id, 'source_hash':document['source_hash'],
            'kind':'received_invoice', 'book_year':date.fromisoformat(document['issued_on']).year,
            'source_date':document['issued_on']}

    def sf_row(row, activity, concept, *, acquisition=False):
        requirement = sf_requirement(row, acquisition=acquisition)
        reg_number = number(requirement)
        day = row['tax_date']
        common_fields = {'autoliquidacion_ejercicio': day.year,
            'autoliquidacion_periodo': f'{(day.month-1)//3+1}T', **common._activity_columns(activity),
            'tipo_factura': 'SF', 'fecha_expedicion': day.strftime('%d/%m/%Y'),
            'fecha_operacion': day.strftime('%d/%m/%Y'), 'referencia_externa': _hash(row['transaction_id'])[:40],
            'transaction_id': row['transaction_id'], 'source_hash': requirement['source_hash']}
        amount = str(row['taxable_base_eur'] if row['kind']=='income' else row['deductible_irpf_eur'])
        common_fields.update(base_imponible_eur=amount, total_factura_eur=amount,
            tipo_iva_percent='0', cuota_iva_soportado_eur='0', cuota_deducible_eur='0',
            cuota_iva_repercutida_eur='0')
        if row['kind']=='income':
            return common_fields | {'concepto_ingreso': concept, 'ingreso_computable_eur': str(row['taxable_base_eur']),
                'factura_numero': reg_number}
        return common_fields | {'concepto_gasto': concept, 'gasto_deducible_eur': str(row['deductible_irpf_eur']),
            'numero_recepcion': reg_number}

    for row in register['rows']:
        key = row['transaction_id']; parent = row['source_transaction_id'] or key
        try:
            raw = sources[parent]
            activity = common._activity_for_transaction(raw, activities)
            acquisition = parent in acquisitions
            depreciation = bool(row['amortization_entry_id'] or
                (row['asset_id'] and row['aeat_expense_concept']=='G03'))
            concept = None
            if row['component'].startswith('fx_'):
                if tax=='iva':
                    raise ValueError('FX entry cannot enter the IVA book')
                if not row['fx_evidence_ref']:
                    raise ValueError('FX entry lacks independent evidence')
                concept = 'I07' if row['kind']=='income' else 'G24'
            elif depreciation:
                concept = 'G31'
            elif row['tax_code']=='historical_g45' or row['document_type']=='social_security_evidence':
                concept = 'G45'
            elif row['document_type'] in {'bank_fee_evidence','internal_bank_fee_reconstruction'}:
                concept = 'G24'
            if concept and (not acquisition or row['component']!='transaction'):
                if tax=='irpf':
                    if raw.get('document_lifecycle') not in {'approved','posted','included_in_snapshot'}:
                        raise ValueError('Non-invoice source document is not approved')
                    if not row.get('document_source_hash') and not row.get('fx_evidence_ref'):
                        raise ValueError('Non-invoice entry lacks source evidence')
                    mapped = sf_row(row, activity, concept)
                    result['income_rows' if row['kind']=='income' else 'expense_rows'].append(mapped)
                    result['lineage'].append({'row_id':key, 'source_transaction_id':parent,
                        'component':row['component'], 'attribution_id':row['attribution_id']})
                continue
            treatment = common._single_treatment(database, raw)
            if row['kind']=='income':
                mapped = common._income_row(raw, treatment, activity)
                if tax=='irpf':
                    mapped['ingreso_computable_eur'] = str(row['taxable_base_eur'])
                    mapped['importe_retenido_irpf_eur'] = str(row['withholding_eur'])
                    mapped['tipo_retencion_irpf_percent'] = common._withholding_rate(
                        _minor(row['taxable_base_eur']), _minor(row['withholding_eur']))
                else:
                    mapped.update(base_imponible_eur=str(row['taxable_base_eur']),
                        total_factura_eur=str(row['amount_eur']), cuota_iva_repercutida_eur=str(row['vat_eur']))
                mapped_rows = [mapped]
            else:
                if row['attribution_id']:
                    raise ValueError('Attributed expense requires a separately reviewed invoice-component mapping')
                treatment = dict(treatment)
                treatment['deductible_irpf_minor'] = 0 if acquisition and concept else _minor(row['deductible_irpf_eur'])
                mapped_rows = common._expense_rows(raw, treatment, activity)
                reg_number = number(invoice_requirement(row['document_id']), raw.get('receipt_number') or '')
                for mapped in mapped_rows:
                    mapped['numero_recepcion'] = reg_number
                    if acquisition:
                        mapped['concepto_gasto'] = '' if _minor(row['deductible_irpf_eur'])==0 or concept else mapped['concepto_gasto']
                    if row['tax_code'] in {'non_eu_service_expense','domestic_reverse_charge_expense','eu_service_expense'}:
                        mapped['total_factura_eur'] = mapped['base_imponible_eur']
                if tax=='irpf' and concept and row['deductible_irpf_eur']:
                    result['expense_rows'].append(sf_row(row, activity, concept, acquisition=True))
                if tax=='irpf':
                    vat_expenses = []
                    for mapped in mapped_rows:
                        base = _minor(mapped['base_imponible_eur'])
                        deduction = _minor(mapped['gasto_deducible_eur'])
                        excess = deduction-base
                        if excess > 0 and mapped['concepto_gasto'] != 'G25':
                            non_deductible_vat = _minor(mapped['cuota_iva_soportado_eur'] or 0)-_minor(mapped['cuota_deducible_eur'] or 0)
                            if excess > non_deductible_vat:
                                raise ValueError('IRPF expense above base lacks a non-deductible IVA allocation')
                            mapped['gasto_deducible_eur'] = common._money(base)
                            # AEAT LSI: non-deductible IVA uses a G25 line of the same invoice.
                            vat_expenses.append(mapped | {'concepto_gasto':'G25',
                                'gasto_deducible_eur':common._money(excess), 'total_factura_eur':'0.00',
                                'base_imponible_eur':'0.00', 'tipo_iva_percent':'0',
                                'cuota_iva_soportado_eur':'0.00', 'cuota_deducible_eur':'0.00',
                                'tipo_retencion_irpf_percent':'', 'importe_retenido_irpf_eur':'',
                                'book_component':mapped['book_component']+':non_deductible_vat'})
                    mapped_rows.extend(vat_expenses)
            for mapped in mapped_rows:
                mapped['referencia_externa'] = _hash({'row':key,'component':mapped.get('book_component','invoice')})[:40]
                mapped['autoliquidacion_ejercicio'] = row['tax_date'].year
                mapped['autoliquidacion_periodo'] = f'{(row["tax_date"].month-1)//3+1}T'
                decision = (operation_dates or {}).get(parent, {}).get(tax)
                if decision:
                    if decision.get('source_hash') != row['document_source_hash'] or not decision.get('evidence_ref'):
                        raise ValueError('Operation-date evidence is missing or stale')
                    mapped['fecha_operacion'] = date.fromisoformat(decision['operation_on']).strftime('%d/%m/%Y')
                operation = date.fromisoformat(raw['transaction_date']) if not decision else date.fromisoformat(decision['operation_on'])
                if (operation.year,(operation.month-1)//3) != (row['tax_date'].year,(row['tax_date'].month-1)//3):
                    raise ValueError('Tax period differs from evidenced operation date; explicit mapping review required')
                result['income_rows' if row['kind']=='income' else 'expense_rows'].append(mapped)
            result['lineage'].append({'row_id':key, 'source_transaction_id':parent,
                'include_modelo130':row['include_modelo130'], 'include_modelo303':row['include_modelo303'],
                'attribution_id':row['attribution_id']})
        except (ValueError, KeyError, TypeError) as exc:
            blocker(key, str(exc))
    if tax=='irpf':
        asset_rows, asset_blockers = common._asset_book_rows(database, activities=activities, year=year, through_date=end)
        result['blockers'].extend(asset_blockers)
        for mapped in asset_rows:
            mapped['referencia_externa'] = mapped['asset_id']
            asset = next(a for a in assets if a['asset_id']==mapped['asset_id'])
            transaction = database.connection.execute('SELECT document_id FROM transactions WHERE transaction_id=?', (asset['acquisition_transaction_id'],)).fetchone()
            document_id = (transaction[0] if transaction else None) or asset['document_id']
            try:
                mapped['numero_recepcion'] = number(invoice_requirement(document_id), mapped.get('numero_recepcion') or '')
            except (ValueError, TypeError) as exc:
                blocker(mapped['asset_id'], str(exc))
        result['asset_rows'] = asset_rows
    else:
        result['asset_scope'] = 'No IVA regularization book is inferred from IRPF asset ownership; separate reviewed regularization required.'
        if quarter == 4 and any(a['cost_minor'] > 300506 for a in assets):
            blocker(period_key, 'Year-end IVA investment-good regularization requires a separate reviewed determination')
    for kind, columns in [('income',INCOME_COLUMNS),('expense',EXPENSE_COLUMNS),('asset',ASSET_COLUMNS)]:
        other = 'irpf' if tax=='iva' else 'iva'
        fields = {column['projection_key'] for column in columns if column['letter'] in EXCLUSIVE[kind][other]}
        for row in result[kind+'_rows']:
            for field in fields: row[field] = ''
    if tax=='irpf':
        for source in register['rows']:
            income = source['kind']=='income'
            rows = result['income_rows' if income else 'expense_rows']
            field = 'ingreso_computable_eur' if income else 'gasto_deducible_eur'
            expected = source['taxable_base_eur' if income else 'deductible_irpf_eur']
            actual = sum((_minor(row[field]) for row in rows if row['transaction_id']==source['transaction_id']),0)
            if actual != _minor(expected):
                blocker(source['transaction_id'], 'IRPF components do not preserve the reviewed source amount')
    result['ready'] = not result['blockers']
    covered = {row['row_id'] for row in result['lineage']}
    if covered != set(result['source_row_ids']):
        blocker(period_key, 'Not all source rows have a mapped lineage entry')
        result['ready'] = False
    result['projection_sha256'] = _hash(result)
    return result


def write_separate_xlsx(template_path: Path, projection: Mapping[str, Any], out_path: Path, *,
                        expected_template_sha256: str = OFFICIAL_TEMPLATE_SHA256) -> Path:
    if not projection.get('ready') or projection.get('blockers'):
        raise ValueError('Separate book has unresolved content or evidence blockers')
    if projection.get('projection_sha256') != _hash({k:v for k,v in projection.items() if k!='projection_sha256'}):
        raise ValueError('Separate book projection changed after review')
    check = inspect_aeat_template(template_path, expected_sha256=expected_template_sha256)
    if not check['valid']:
        raise ValueError('Official template contract is not valid')
    tax = projection['tax']; sheets = []
    with zipfile.ZipFile(template_path) as archive:
        names = _workbook_sheets(archive); strings = _shared_strings(archive)
        for kind, source, target, columns in [
            ('income','EXPEDIDAS_INGRESOS','EXPEDIDAS' if tax=='iva' else 'INGRESOS',INCOME_COLUMNS),
            ('expense','RECIBIDAS_GASTOS','RECIBIDAS' if tax=='iva' else 'GASTOS',EXPENSE_COLUMNS),
            ('asset','BIENES-INVERSIÓN','BIENES-INVERSIÓN',ASSET_COLUMNS)]:
            if kind=='asset' and not projection['asset_rows']: continue
            headers = _sheet_rows(archive,names[source],strings,through_row=3)
            values = [headers[i] for i in [1,2,3]]
            for row in projection[kind+'_rows']:
                cells = []
                for column in columns:
                    value = row.get(column['projection_key'])
                    if value in {None,''}: cells.append(None)
                    elif column['value_type']=='integer': cells.append(int(value))
                    elif column['value_type']=='decimal': cells.append(Decimal(str(value)))
                    elif column['value_type']=='date':
                        day,month,year = str(value).split('/');cells.append(date(int(year),int(month),int(day)))
                    else:
                        text = str(value)
                        limit = re.search(r'Alfanum.rico\s*\((\d+)\)', headers[3][column['index']-1], re.I)
                        if limit and len(text) > int(limit[1]):
                            raise ValueError(f'{source} {column["letter"]}: text exceeds the official field length')
                        cells.append(text)
                values.append(cells)
            sheets.append((target,values))
    from .tax_register_workbook import write_table_workbook
    return write_table_workbook(out_path,sheets,exclusive=True,max_bytes=MAX_AEAT_WORKBOOK_BYTES)
