# Table flattening for injected vault chunks. The failure this pins:
# asked for a CURRENT measurement whose Current cell is a dash, FRED
# answered with the Target column's value instead.
#
# Fixture values are SYNTHETIC on purpose. The real table lives in the
# vault's personal/ tree, which is marked sensitive and which rules.md
# forbids copying anywhere off the machine — "no export, no repo"
# names this case exactly. The shape is what's under test, not the
# numbers, so inventing them costs the test nothing.

from utils.vault_md import flatten_tables

FITNESS_TABLE = """## Biometrics

| Measure | Baseline | Current | Target |
|---|---|---|---|
| Height | — | 100 cm | — |
| Weight | 10.0 kg (Apr 2000) | **20.0 kg** (Jul 2000) | 30.0 kg for July |
| Shoulder circumference | 11" | — | 22" |
"""


def test_each_value_is_bound_to_its_column_name():
    flat = flatten_tables(FITNESS_TABLE)
    assert 'Shoulder circumference — Baseline: 11"' in flat
    assert 'Target: 22"' in flat


def test_missing_current_is_explicit_not_omitted():
    """
    The dash must survive as a stated "(not recorded)". Dropping it is
    what let the model reach into the neighbouring column for a number.
    """
    flat = flatten_tables(FITNESS_TABLE)
    shoulder = [l for l in flat.split("\n") if l.startswith("Shoulder")][0]
    assert "Current: (not recorded)" in shoulder
    # and the target must not be sitting in the Current slot
    assert 'Current: 22"' not in shoulder


def test_real_current_values_are_preserved():
    flat = flatten_tables(FITNESS_TABLE)
    assert "Current: 100 cm" in flat
    assert "Current: **20.0 kg** (Jul 2000)" in flat


def test_non_table_text_is_untouched():
    prose = "## Notes\n\nHe trains five days a week.\n\n- one\n- two"
    assert flatten_tables(prose) == prose


def test_heading_above_a_table_survives():
    flat = flatten_tables(FITNESS_TABLE)
    assert "## Biometrics" in flat
