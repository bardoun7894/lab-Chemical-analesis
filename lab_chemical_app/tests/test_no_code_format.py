"""No. Code format: one letter followed by exactly four digits.

The field was free text until 2026-08-31, which let PIP10, X101, Z and even a
lorem-ipsum sentence into the production identifier column. These tests pin the
write-side guard; historical rows are cleaned separately.
"""

import pytest

from app.models.pipe import normalize_no_code


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("N8739", "N8739"),
        ("n8739", "N8739"),  # case is normalized up
        ("  c0001  ", "C0001"),  # surrounding whitespace is stripped
        ("Z0003", "Z0003"),
    ],
)
def test_valid_codes_are_accepted_and_upper_cased(raw, expected):
    code, error = normalize_no_code(raw)
    assert error is None
    assert code == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "PIP10",  # three letters
        "PIP1",
        "X101",  # three digits
        "X10",
        "K1",
        "P10000",  # five digits
        "Z",  # letter only
        "8739N",  # digits first
        "N873",
        "N87390",
        "N-8739",
        "DEMO-01N1",
        "Libero id quis quis",
    ],
)
def test_invalid_codes_are_rejected(raw):
    code, error = normalize_no_code(raw)
    assert code is None
    assert error


def test_error_message_names_the_rule():
    _, error = normalize_no_code("PIP10")
    assert "PIP10" in error
    assert "four digits" in error
