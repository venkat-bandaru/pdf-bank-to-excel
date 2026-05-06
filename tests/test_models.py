"""Tests for model invariants defined in models.py.

Covers: Transaction frozen constraint, exactly-one-money-direction rule,
and Decimal enforcement. Also smoke-tests RawRow and Statement construction.
"""

from __future__ import annotations

import datetime
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.models import Config, RawRow, Statement, Transaction

_DATE = datetime.date(2024, 1, 15)
_DESC = "AMAZON PAYMENT"
_TEN = Decimal("10.00")


# ---------------------------------------------------------------------------
# Transaction — frozen
# ---------------------------------------------------------------------------


def test_transaction_is_frozen() -> None:
    t = Transaction(date=_DATE, description=_DESC, money_out=_TEN, money_in=None, balance=None, confidence="ok")
    with pytest.raises(FrozenInstanceError):
        t.description = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Transaction — exactly one of money_out / money_in
# ---------------------------------------------------------------------------


def test_transaction_money_out_only() -> None:
    t = Transaction(date=_DATE, description=_DESC, money_out=_TEN, money_in=None, balance=None, confidence="ok")
    assert t.money_out == _TEN
    assert t.money_in is None


def test_transaction_money_in_only() -> None:
    t = Transaction(date=_DATE, description=_DESC, money_out=None, money_in=_TEN, balance=None, confidence="ok")
    assert t.money_in == _TEN
    assert t.money_out is None


def test_transaction_both_set_raises() -> None:
    with pytest.raises(ValueError, match="Exactly one"):
        Transaction(date=_DATE, description=_DESC, money_out=_TEN, money_in=_TEN, balance=None, confidence="ok")


def test_transaction_neither_set_raises() -> None:
    with pytest.raises(ValueError, match="Exactly one"):
        Transaction(date=_DATE, description=_DESC, money_out=None, money_in=None, balance=None, confidence="ok")


# ---------------------------------------------------------------------------
# Transaction — Decimal enforcement
# ---------------------------------------------------------------------------


def test_transaction_float_money_out_raises() -> None:
    with pytest.raises(TypeError, match="money_out must be Decimal"):
        Transaction(
            date=_DATE,
            description=_DESC,
            money_out=10.0,  # type: ignore[arg-type]
            money_in=None,
            balance=None,
            confidence="ok",
        )


def test_transaction_float_money_in_raises() -> None:
    with pytest.raises(TypeError, match="money_in must be Decimal"):
        Transaction(
            date=_DATE,
            description=_DESC,
            money_out=None,
            money_in=10.0,  # type: ignore[arg-type]
            balance=None,
            confidence="ok",
        )


def test_transaction_int_money_raises() -> None:
    with pytest.raises(TypeError, match="money_out must be Decimal"):
        Transaction(
            date=_DATE,
            description=_DESC,
            money_out=10,  # type: ignore[arg-type]
            money_in=None,
            balance=None,
            confidence="ok",
        )


def test_transaction_decimal_balance_accepted() -> None:
    t = Transaction(
        date=_DATE,
        description=_DESC,
        money_out=_TEN,
        money_in=None,
        balance=Decimal("500.00"),
        confidence="ok",
    )
    assert t.balance == Decimal("500.00")


def test_transaction_none_balance_accepted() -> None:
    t = Transaction(date=_DATE, description=_DESC, money_out=_TEN, money_in=None, balance=None, confidence="ok")
    assert t.balance is None


# ---------------------------------------------------------------------------
# RawRow — basic construction (strings only, not frozen)
# ---------------------------------------------------------------------------


def test_rawrow_construction() -> None:
    row = RawRow(date="15 Jan 2024", description="AMAZON", money_out="10.00", money_in="", balance="490.00")
    assert row.date == "15 Jan 2024"
    assert row.money_out == "10.00"


def test_rawrow_is_mutable() -> None:
    row = RawRow(date="15 Jan 2024", description="AMAZON", money_out="10.00", money_in="", balance="490.00")
    row.description = "EDITED"
    assert row.description == "EDITED"


# ---------------------------------------------------------------------------
# Statement — basic construction
# ---------------------------------------------------------------------------


def test_statement_construction() -> None:
    t = Transaction(date=_DATE, description=_DESC, money_out=_TEN, money_in=None, balance=None, confidence="ok")
    stmt = Statement(source_pdf=Path("input/test.pdf"), bank="hsbc", transactions=[t])
    assert stmt.bank == "hsbc"
    assert len(stmt.transactions) == 1


# ---------------------------------------------------------------------------
# Config — frozen dataclass
# ---------------------------------------------------------------------------


def test_config_is_frozen(tmp_path: Path) -> None:
    cfg = Config(
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        failed_dir=tmp_path / "failed",
        log_dir=tmp_path / "logs",
        detect_min_chars_per_page=100,
        extractor_priority=("hsbc", "barclays", "generic"),
    )
    with pytest.raises(FrozenInstanceError):
        cfg.detect_min_chars_per_page = 200  # type: ignore[misc]
