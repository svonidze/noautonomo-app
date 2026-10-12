from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

import pytest

from autonomo_taxes.history import RawXoloExpense
from autonomo_taxes.modelo303 import (
    Modelo303CasillaEvidence,
    REVERSE_CHARGE_BOXES,
    _raw_vat_bearing_totals_from_csv,
    _raw_vat_bearing_totals_by_period,
    _settlement_casilla_evidence_from_fragments,
    _settlement_casillas_from_fragments,
    _structural_casilla_evidence_from_pages,
    values_from_monetary_sequence,
)


class Modelo303Tests(unittest.TestCase):
    def test_structural_layout_extracts_reverse_charge_and_blank_asset_boxes(self):
        layout = """
        Otras operaciones con inversion del sujeto pasivo 12  701,80 13 147,38
        Una fila no relacionada termina con 120 17.460,21
        Total cuota devengada ................................ 27 147,38
        Operaciones interiores corrientes .................... 28 2.254,8329 473,52
        Operaciones interiores con bienes de inversion ....... 30 31
        Total a deducir ...................................... 45473,52
        Resultado regimen general ............................ 46 -326,14
        """
        positioned = [[
            ("12", Decimal("296.1"), Decimal("316.6"), Decimal("1.0")),
            ("701,80", Decimal("364.2"), Decimal("314.5"), Decimal("9.0")),
            ("13", Decimal("0.0"), Decimal("0.0"), Decimal("1.0")),
            ("27", Decimal("460.3"), Decimal("217.4"), Decimal("1.0")),
            ("147,38", Decimal("528.8"), Decimal("216.2"), Decimal("9.0")),
            ("28", Decimal("360.7"), Decimal("182.6"), Decimal("1.0")),
            ("2.254,83", Decimal("420.7"), Decimal("181.6"), Decimal("9.0")),
            ("29", Decimal("460.7"), Decimal("182.6"), Decimal("1.0")),
            ("30", Decimal("360.7"), Decimal("170.6"), Decimal("1.0")),
            ("31", Decimal("460.7"), Decimal("170.6"), Decimal("1.0")),
            ("45", Decimal("460.4"), Decimal("56.5"), Decimal("1.0")),
            ("473,52", Decimal("528.3"), Decimal("55.5"), Decimal("9.0")),
            ("46", Decimal("460.4"), Decimal("30.1"), Decimal("1.0")),
            ("-326,14", Decimal("525.6"), Decimal("28.4"), Decimal("9.0")),
        ]]

        evidence = _structural_casilla_evidence_from_pages([layout], positioned)

        self.assertEqual(evidence.status, "casillas_extracted")
        self.assertEqual(
            dict(evidence.casillas),
            {
                "12": Decimal("701.80"),
                "13": Decimal("147.38"),
                "27": Decimal("147.38"),
                "28": Decimal("2254.83"),
                "29": Decimal("473.52"),
                "30": Decimal("0.00"),
                "31": Decimal("0.00"),
                "45": Decimal("473.52"),
                "46": Decimal("-326.14"),
            },
        )
        self.assertEqual(evidence.blank_casillas, ("30", "31"))
        self.assertEqual(dict(evidence.value_sources)["13"], "layout_line")
        self.assertEqual(dict(evidence.value_sources)["45"], "layout_line+positioned_row")

    def test_structural_layout_distinguishes_printed_zero_from_blank_box(self):
        evidence = _structural_casilla_evidence_from_pages(
            ["Operaciones interiores con bienes de inversion 30 0,00 31 0,00"],
            [[]],
        )

        self.assertEqual(evidence.status, "incomplete_structural_layout")
        self.assertEqual(
            dict(evidence.casillas),
            {"30": Decimal("0.00"), "31": Decimal("0.00")},
        )
        self.assertEqual(evidence.blank_casillas, ())
        self.assertEqual(dict(evidence.value_sources)["30"], "layout_line")

    def test_settlement_layout_extracts_compensation_boxes_and_blank_zeroes(self):
        page = [
            ("64", Decimal("462.8"), Decimal("548.4"), Decimal("1.0")),
            ("-45,36", Decimal("530.7"), Decimal("548.1"), Decimal("9.0")),
            ("110", Decimal("461.1"), Decimal("497.4"), Decimal("1.0")),
            ("876,91", Decimal("528.7"), Decimal("496.4"), Decimal("9.0")),
            ("78", Decimal("462.8"), Decimal("480.4"), Decimal("1.0")),
            ("87", Decimal("462.8"), Decimal("463.4"), Decimal("1.0")),
            ("876,91", Decimal("528.7"), Decimal("462.7"), Decimal("9.0")),
            ("69", Decimal("462.8"), Decimal("436.8"), Decimal("1.0")),
            ("-45,36", Decimal("531.7"), Decimal("435.8"), Decimal("9.0")),
            ("71", Decimal("462.8"), Decimal("388.8"), Decimal("1.0")),
            ("-45,36", Decimal("531.7"), Decimal("386.7"), Decimal("9.0")),
        ]
        final_page = [
            ("72", Decimal("88.2"), Decimal("739.9"), Decimal("1.0")),
            ("45,36", Decimal("161.6"), Decimal("737.2"), Decimal("9.0")),
            ("73", Decimal("161.3"), Decimal("527.1"), Decimal("1.0")),
        ]

        casillas, status = _settlement_casillas_from_fragments([page, final_page])

        self.assertEqual(status, "casillas_extracted")
        self.assertEqual(
            dict(casillas),
            {
                "64": Decimal("-45.36"),
                "110": Decimal("876.91"),
                "78": Decimal("0.00"),
                "87": Decimal("876.91"),
                "69": Decimal("-45.36"),
                "71": Decimal("-45.36"),
                "72": Decimal("45.36"),
                "73": Decimal("0.00"),
            },
        )

        evidence = _settlement_casilla_evidence_from_fragments([page, final_page])
        self.assertIn("78", evidence.blank_casillas)
        self.assertEqual(
            dict(evidence.value_sources)["78"],
            "box_present_no_value_captured",
        )
        self.assertEqual(dict(evidence.value_sources)["110"], "positioned_row")

    def test_settlement_layout_extracts_q4_refund(self):
        final_page = [
            ("72", Decimal("88.2"), Decimal("739.9"), Decimal("1.0")),
            ("73", Decimal("161.3"), Decimal("527.1"), Decimal("1.0")),
            ("170,95", Decimal("189.0"), Decimal("525.6"), Decimal("9.0")),
        ]

        casillas, status = _settlement_casillas_from_fragments([final_page])

        self.assertEqual(status, "incomplete_settlement_layout")
        self.assertEqual(dict(casillas), {"72": Decimal("0.00"), "73": Decimal("170.95")})

    def test_deductible_only_sequence_maps_to_local_input_vat(self):
        values = values_from_monetary_sequence([Decimal("776.66"), Decimal("161.20"), Decimal("-161.20")])

        self.assertEqual(values.extraction_status, "deductible_only")
        self.assertEqual(values.output_vat, Decimal("0.00"))
        self.assertEqual(values.net_local_input_base, Decimal("776.66"))
        self.assertEqual(values.net_local_input_vat, Decimal("161.20"))

    def test_reverse_charge_sequence_nets_output_from_deductible_vat(self):
        values = values_from_monetary_sequence(
            [
                Decimal("701.80"),
                Decimal("147.38"),
                Decimal("2254.83"),
                Decimal("473.52"),
                Decimal("-326.14"),
            ]
        )

        self.assertEqual(values.extraction_status, "output_and_deductible")
        self.assertEqual(values.output_base, Decimal("701.80"))
        self.assertEqual(values.output_vat, Decimal("147.38"))
        self.assertEqual(values.net_local_input_base, Decimal("1553.03"))
        self.assertEqual(values.net_local_input_vat, Decimal("326.14"))

    def test_reverse_charge_only_sequence_sums_deductible_bases(self):
        values = values_from_monetary_sequence(
            [
                Decimal("1000.00"),  # 10: reverse-charge services base
                Decimal("210.00"),  # 27: total output VAT
                Decimal("400.00"),  # 28: domestic deductible base
                Decimal("1000.00"),  # 36: reverse-charge deductible base
                Decimal("294.00"),  # 45: total deductible VAT
                Decimal("-84.00"),  # 46/71: result
            ],
            structural_evidence=_reverse_evidence({
                "10": "1000", "11": "210", "27": "210", "28": "400", "29": "84",
                "36": "1000", "37": "210", "45": "294", "46": "-84",
            }),
        )

        self.assertEqual(values.extraction_status, "reverse_charge_services")
        self.assertEqual(values.output_base, Decimal("1000.00"))
        self.assertEqual(values.output_vat, Decimal("210.00"))
        self.assertEqual(values.deductible_base, Decimal("1400.00"))
        self.assertEqual(values.deductible_vat, Decimal("294.00"))
        self.assertEqual(values.result, Decimal("-84.00"))
        self.assertEqual(values.net_local_input_base, Decimal("400.00"))
        self.assertEqual(values.net_local_input_vat, Decimal("84.00"))

    def test_reverse_charge_with_other_output_sequence_sums_both_bases(self):
        values = values_from_monetary_sequence(
            [
                Decimal("1000.00"),  # 10: reverse-charge services base
                Decimal("200.00"),  # 12: other reverse-charge base
                Decimal("252.00"),  # 27: total output VAT
                Decimal("600.00"),  # 28: domestic deductible base
                Decimal("1000.00"),  # 36: reverse-charge deductible base
                Decimal("336.00"),  # 45: total deductible VAT
                Decimal("-84.00"),  # 46/71: result
            ],
            structural_evidence=_reverse_evidence({
                "10": "1000", "11": "210", "12": "200", "13": "42", "27": "252",
                "28": "600", "29": "126", "36": "1000", "37": "210", "45": "336", "46": "-84",
            }),
        )

        self.assertEqual(values.extraction_status, "reverse_charge_with_other_output")
        self.assertEqual(values.output_base, Decimal("1200.00"))
        self.assertEqual(values.output_vat, Decimal("252.00"))
        self.assertEqual(values.deductible_base, Decimal("1600.00"))
        self.assertEqual(values.deductible_vat, Decimal("336.00"))
        self.assertEqual(values.result, Decimal("-84.00"))
        self.assertEqual(values.net_local_input_base, Decimal("400.00"))
        self.assertEqual(values.net_local_input_vat, Decimal("84.00"))

    def test_raw_vat_bearing_totals_ignore_usd_and_zero_vat_rows(self):
        rows = [
            RawXoloExpense(
                date=date(2025, 4, 1),
                recipient="Xolo",
                expense_type="Professional expenses",
                number="INV",
                amount_original=Decimal("121.00"),
                currency="EUR",
                subtotal_amount=Decimal("100.00"),
            ),
            RawXoloExpense(
                date=date(2025, 4, 2),
                recipient="Reverse charge supplier",
                expense_type="Professional expenses",
                number="RC",
                amount_original=Decimal("50.00"),
                currency="EUR",
                subtotal_amount=Decimal("50.00"),
            ),
            RawXoloExpense(
                date=date(2025, 4, 3),
                recipient="USD supplier",
                expense_type="Professional expenses",
                number="USD",
                amount_original=Decimal("20.00"),
                currency="USD",
                subtotal_amount=Decimal("20.00"),
            ),
        ]

        totals = _raw_vat_bearing_totals_by_period(rows)

        self.assertEqual(totals["2025-Q2"]["base"], Decimal("100.00"))
        self.assertEqual(totals["2025-Q2"]["vat"], Decimal("21.00"))

    def test_csv_raw_totals_use_taxable_base_for_exempt_base_rows(self):
        csv_text = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,"
            "gross_amount,match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status\n"
            ',1,Synthetic Party 013,Professional expenses,SYNTH-DOCUMENT-033,2026-03-31,,"€99,08",99.08,EUR,99.08,99.08,82.67,16.41,21,0,0,UNPAID\n'
            ',2,Xolo Business Spain,Professional expenses,INV,2026-03-01,,"€71,39",71.39,EUR,71.39,71.39,59.00,12.39,21,0,0,UNPAID\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.csv"
            path.write_text(csv_text, encoding="utf-8")

            totals = _raw_vat_bearing_totals_from_csv(path)

        self.assertEqual(totals["2026-Q1"]["vat"], Decimal("28.80"))
        self.assertEqual(totals["2026-Q1"]["base"], Decimal("137.14"))




def _reverse_evidence(overrides):
    boxes = {key: Decimal(0) for key in REVERSE_CHARGE_BOXES}
    boxes.update({key: Decimal(value) for key, value in overrides.items()})
    return Modelo303CasillaEvidence(tuple(boxes.items()), (), (), "casillas_extracted")


def _synthetic_reverse_charge_pdf(path, *, repeated_vat=False, previous_compensation=False):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    empty = DecodedStreamObject()
    empty.set_data(b" ")
    page[NameObject("/Contents")] = writer._add_object(empty)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)

    def add_page(items):
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        commands = ["BT"]
        for text, x, y, size in items:
            commands.append(f"1 0 0 1 {x} {y} Tm /F1 {size} Tf ({text}) Tj")
        commands.append("/F1 1 Tf ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode())
        page[NameObject("/Contents")] = writer._add_object(stream)

    if repeated_vat:
        boxes = {"10": "1000.00", "11": "210.00", "27": "210.00", "36": "1000.00", "37": "210.00", "45": "210.00", "46": "0.00"}
    elif previous_compensation:
        boxes = {"10": "1000.00", "11": "210.00", "27": "210.00", "28": "500.00", "29": "0.00", "36": "1000.00", "37": "105.00", "45": "105.00", "46": "105.00"}
    else:
        boxes = {"10": "1000.00", "11": "210.00", "27": "210.00", "28": "400.00", "29": "84.00", "36": "1000.00", "37": "210.00", "45": "294.00", "46": "-84.00"}
    groups = [("150",), ("01",), ("04",), ("07",), ("10", "11"), ("12", "13"), ("27",), ("28", "29"), ("30", "31"), ("32", "33"), ("34", "35"), ("36", "37"), ("38", "39"), ("45",), ("46",)]
    items = []
    for index, group in enumerate(groups):
        y = 790 - index * 24
        for column, key in enumerate(group):
            x = 60 + column * 240
            items.append((key, x, y, 1))
            if key in boxes:
                size = 9 if repeated_vat or key not in {"11", "29", "37"} else 4
                items.append((boxes[key].replace(".", ","), x + 45, y, size))
    add_page(items)
    if previous_compensation:
        items = []
        positions = {"64": (460, 550), "110": (460, 500), "78": (460, 480), "87": (460, 460), "69": (460, 435), "71": (460, 385), "72": (88, 740), "73": (161, 527)}
        for key, (x, y) in positions.items():
            value = "105,00" if key in {"64", "110", "78"} else "0,00"
            items.extend([(key, x, y, 1), (value, x + 45, y, 9)])
        add_page(items)
    writer.write(path)


def test_reverse_charge_lengths_without_casilla_evidence_stay_unverified():
    for values in ([1000, 210, 400, 1000, 294, -84], [1000, 210, 210, 1000, 210, 210, 0]):
        parsed = values_from_monetary_sequence([Decimal(x) for x in values])
        assert parsed.extraction_status.startswith("unexpected_monetary_sequence")


def test_reverse_charge_pdf_requires_matching_structural_evidence(tmp_path):
    from autonomo_taxes.modelo303 import extract_modelo303_values

    path = tmp_path / "synthetic-303.pdf"
    _synthetic_reverse_charge_pdf(path)
    parsed = extract_modelo303_values(path)
    assert parsed.extraction_status == "reverse_charge_services"
    assert parsed.output_base == Decimal("1000.00")
    assert parsed.deductible_base == Decimal("1400.00")
    assert parsed.result == Decimal("-84.00")
    assert parsed.structural_extraction_status == "casillas_extracted"

    _synthetic_reverse_charge_pdf(path, repeated_vat=True)
    ambiguous = extract_modelo303_values(path)
    assert len(ambiguous.monetary_sequence) == 7
    assert ambiguous.extraction_status == "unexpected_monetary_sequence_len_7"


def test_reverse_charge_pdf_uses_final_settlement_after_compensation(tmp_path):
    from autonomo_taxes.modelo303 import extract_modelo303_values

    path = tmp_path / "synthetic-303-compensation.pdf"
    _synthetic_reverse_charge_pdf(path, previous_compensation=True)
    parsed = extract_modelo303_values(path)
    assert parsed.extraction_status == "reverse_charge_services"
    assert dict(parsed.structural_casillas)["46"] == Decimal("105.00")
    assert parsed.result == Decimal("0.00")
    assert parsed.settlement_values["110"] == Decimal("105.00")
    assert parsed.settlement_values["78"] == Decimal("105.00")
    assert parsed.compensation_carryforward == Decimal("0.00")


def _receipt_pdf(path, pages):
    """Write a receipt whose fragments are (text, x, y, font operand, text-matrix scale)."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    for items in pages:
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        commands = ["BT"]
        for text, x, y, size, scale in items:
            commands.append(f"{scale} 0 0 {scale} {x} {y} Tm /F1 {size} Tf ({text}) Tj")
        commands.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)


_FORM_GROUPS = [("150",), ("01",), ("04",), ("07",), ("10", "11"), ("12", "13"), ("27",), ("28", "29"), ("30", "31"),
                ("32", "33"), ("34", "35"), ("36", "37"), ("38", "39"), ("45",), ("46",)]
_SETTLEMENT_POSITIONS = {"64": (460, 550), "110": (460, 500), "78": (460, 480), "87": (460, 460),
                         "69": (460, 435), "71": (460, 385), "72": (88, 740), "73": (161, 527)}
_RC_BOXES = {"10": "1.000,00", "11": "210,00", "27": "210,00", "28": "400,00", "29": "84,00",
             "36": "1.000,00", "37": "210,00", "45": "294,00", "46": "-84,00"}
_RC_SETTLEMENT = {"64": "-84,00", "110": "500,00", "87": "500,00", "69": "-84,00", "71": "-84,00", "72": "84,00"}


def _modelo303_receipt(path, boxes, settlement, *, amount_size, amount_scale, small_amounts=(), extra=()):
    # Casilla labels print at 6.5pt on both provider and Sede receipts.
    label = (1, 6.5)
    form = []
    for index, group in enumerate(_FORM_GROUPS):
        y = 790 - index * 24
        for column, key in enumerate(group):
            x = 60 + column * 240
            form.append((key, x, y, *label))
            if key in boxes:
                size, scale = (4, 1) if key in small_amounts else (amount_size, amount_scale)
                form.append((boxes[key], x + 45, y, size, scale))
    closing = []
    for key, (x, y) in _SETTLEMENT_POSITIONS.items():
        closing.append((key, x, y, *label))
        if key in settlement:
            closing.append((settlement[key], x + 45, y, amount_size, amount_scale))
    _receipt_pdf(path, [[("Modelo 303", 60, 800, 12, 1)], form + list(extra), closing])


@pytest.mark.parametrize(("amount_size", "amount_scale"), [(1, 9), (9, 1)], ids=["sede", "provider"])
def test_receipt_totals_come_from_casillas_when_every_cuota_is_printed(tmp_path, amount_size, amount_scale):
    from autonomo_taxes.modelo303 import extract_modelo303_values

    path = tmp_path / "receipt-303.pdf"
    _modelo303_receipt(path, _RC_BOXES, _RC_SETTLEMENT, amount_size=amount_size, amount_scale=amount_scale)
    parsed = extract_modelo303_values(path)

    assert len(parsed.monetary_sequence) == 9
    assert parsed.extraction_status == "positioned_casillas"
    assert (parsed.output_base, parsed.output_vat) == (Decimal("1000.00"), Decimal("210.00"))
    assert (parsed.deductible_base, parsed.deductible_vat) == (Decimal("1400.00"), Decimal("294.00"))
    assert parsed.result == Decimal("-84.00")
    assert parsed.compensation_carryforward == Decimal("584.00")
    assert dict(parsed.structural_casillas)["29"] == Decimal("84.00")


def test_receipt_flags_a_sequence_layout_that_the_casillas_contradict(tmp_path):
    from autonomo_taxes.modelo303 import extract_modelo303_values

    path = tmp_path / "receipt-303-conflict.pdf"
    _modelo303_receipt(path, _RC_BOXES, _RC_SETTLEMENT, amount_size=9, amount_scale=1,
                       small_amounts=("11", "29", "36", "37"))
    parsed = extract_modelo303_values(path)

    assert len(parsed.monetary_sequence) == 5
    assert parsed.extraction_status == "sequence_and_casillas_disagree"


# An amount printed between the 28 and 30 rows belongs to no casilla read here.
_STRAY_AMOUNT = ("777,00", 105, 610, 9, 1)


def test_receipt_flags_a_sequence_layout_contradicted_despite_an_unexplained_amount(tmp_path):
    from autonomo_taxes.modelo303 import extract_modelo303_values

    path = tmp_path / "receipt-303-stray.pdf"
    _modelo303_receipt(path, _RC_BOXES, _RC_SETTLEMENT, amount_size=9, amount_scale=1,
                       small_amounts=("11", "28", "29", "36", "37"), extra=[_STRAY_AMOUNT])
    parsed = extract_modelo303_values(path)

    assert len(parsed.monetary_sequence) == 5
    assert parsed.extraction_status == "sequence_and_casillas_disagree"


def test_casilla_totals_do_not_replace_a_sequence_with_unexplained_amounts(tmp_path):
    from autonomo_taxes.modelo303 import extract_modelo303_values

    path = tmp_path / "receipt-303-unexplained.pdf"
    _modelo303_receipt(path, _RC_BOXES, _RC_SETTLEMENT, amount_size=1, amount_scale=9, extra=[_STRAY_AMOUNT])
    parsed = extract_modelo303_values(path)

    assert parsed.extraction_status == "unexpected_monetary_sequence_len_10"


@pytest.mark.parametrize("amount", ["210,00", "400,00", "0,00", "21,00"])
@pytest.mark.parametrize("labelled", [False, True], ids=["unassigned", "unsupported-box"])
def test_casilla_totals_reject_unassigned_amounts_equal_to_known_values(tmp_path, amount, labelled):
    from dataclasses import replace

    from autonomo_taxes.filing_evidence import extract_filing_evidence
    from autonomo_taxes.filing_receipts import verify_filing_receipt
    from autonomo_taxes.modelo303 import _crosscheck_status
    from autonomo_taxes.money import parse_amount

    path = tmp_path / "MOD 303 1T 2026 synthetic.pdf"
    extra = [(amount, 105, 610, 9, 1)]
    if labelled:
        extra.insert(0, ("59", 60, 610, 1, 6.5))
    _modelo303_receipt(path, _RC_BOXES, _RC_SETTLEMENT, amount_size=1, amount_scale=9, extra=extra)
    evidence = replace(
        extract_filing_evidence(path),
        filed_on="2026-04-15T12:00:00",
        submission_reference="synthetic-reference",
        justificante_number="3030000000000",
        verification_code="SYNTHETICCSV",
    )
    status = evidence.payload["extraction_status"]
    expected_status = "unexpected_monetary_sequence_len_9" if amount == "21,00" else "unexpected_monetary_sequence_len_10"
    assert status == expected_status
    assert _crosscheck_status(status, Decimal("0.00"), Decimal("0.00")) == "needs_manual_review"

    calculated = {key: Decimal("0.00") for key in REVERSE_CHARGE_BOXES}
    calculated.update({key: parse_amount(raw) for key, raw in {**_RC_BOXES, **_RC_SETTLEMENT}.items()})
    calculated.update({"59": Decimal("0.00"), "compensation_carryforward": Decimal("584.00")})
    with pytest.raises(ValueError, match=expected_status):
        verify_filing_receipt(
            evidence, {"form": "303", "period": "2026-Q1", "values": calculated},
            expected_form="303", expected_period="2026-Q1", calculation_sha256="0" * 64,
        )


@pytest.mark.parametrize(("amount_size", "amount_scale"), [(1, 9), (9, 1)], ids=["sede", "provider"])
def test_casilla_totals_allow_rates_only_in_their_positioned_fields(tmp_path, amount_size, amount_scale):
    from autonomo_taxes.modelo303 import extract_modelo303_values

    path = tmp_path / "receipt-303-with-rate.pdf"
    extra = [("08", 180, 718, 1, 6.5), ("21,00", 225, 718, amount_size, amount_scale),
             ("09", 300, 718, 1, 6.5)]
    _modelo303_receipt(path, _RC_BOXES, _RC_SETTLEMENT, amount_size=amount_size,
                       amount_scale=amount_scale, extra=extra)
    parsed = extract_modelo303_values(path)

    assert parsed.extraction_status == "positioned_casillas"
    assert len(parsed.monetary_sequence) == 9
    assert parsed.output_vat == Decimal("210.00")
    assert parsed.compensation_carryforward == Decimal("584.00")


@pytest.mark.parametrize(
    "broken",
    [
        {"27": "220,00", "46": "-74,00"},  # 27 != 11 + 13
        {"45": "300,00", "46": "-90,00"},  # 45 != 29 + 31 + ... + 39
        {"46": "-90,00"},                  # 46 != 27 - 45
    ],
    ids=["output-vat", "deductible-vat", "result"],
)
def test_casilla_totals_require_the_form_to_add_up(tmp_path, broken):
    from autonomo_taxes.modelo303 import extract_modelo303_values

    path = tmp_path / "receipt-303-inconsistent.pdf"
    _modelo303_receipt(path, {**_RC_BOXES, **broken}, _RC_SETTLEMENT, amount_size=1, amount_scale=9)
    parsed = extract_modelo303_values(path)

    assert parsed.extraction_status == "unexpected_monetary_sequence_len_9"


def test_crosscheck_sends_contradicted_receipts_to_manual_review():
    from autonomo_taxes.modelo303 import _crosscheck_status

    assert _crosscheck_status("sequence_and_casillas_disagree", Decimal("0.00"), Decimal("0.00")) == "needs_manual_review"


if __name__ == "__main__":
    unittest.main()
