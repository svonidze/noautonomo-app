import math

from autonomo_taxes.pdf_text import effective_font_size


def test_effective_font_size_reads_size_from_operand_or_text_matrix():
    assert effective_font_size(9, [1, 0, 0, 1, 0, 0]) == 9
    assert effective_font_size(1, [9, 0, 0, 9, 0, 0]) == 9
    assert effective_font_size(1, [1, 0, 0, 1, 0, 0], [9, 0, 0, 9, 0, 0]) == 9


def test_effective_font_size_measures_height_not_width():
    assert effective_font_size(9, [0.5, 0, 0, 1, 0, 0]) == 9
    assert math.isclose(effective_font_size(1, [0, 9, -9, 0, 0, 0]), 9)
