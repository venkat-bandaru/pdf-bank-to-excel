"""End-to-end reconciliation checks across every known bank extractor.

This enforces the invariant that adding statement-level reconciliation must
not break any bank: every extractor's sample either reconciles cleanly
(extracted rows agree with the printed summary totals) or has no summary at
all and is therefore skipped. A bank that newly fails to reconcile — because
its summary parsing drifted or its extractor started dropping rows — fails
here rather than silently shipping a "REVIEW" to a client's spreadsheet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from statement_to_excel import normalize
from statement_to_excel.extractors.base import SummaryProvider
from statement_to_excel.pipeline import _EXTRACTORS

# Bank -> anonymised sample PDF that its own extractor test also uses.
_SAMPLES = {
    "hsbc": "September.pdf",
    "barclays": "Statement 21-FEB-25.pdf",
    "rbsbank": "rbs_bank.pdf",
    "natwestbank": "Statement-01-04-2025.pdf",
    "lloyds": "2024_August_Statement.pdf",
    "starlingbank": "StarlingStatement_2025-10-01_2025-12-31.pdf",
    "virginmoneybank": "VirginMoney_Statement_2025-01-31.pdf",
    "zemplerbank": "Zempler_transactions_062025.pdf",
    "revolutbank": "revoult_01-Oct-2025.pdf",
    "tidebank": "tide_bank.pdf",
}

# Banks whose statements print no usable summary block: reconciliation is
# deliberately skipped, so they must NOT advertise the SummaryProvider
# capability (the pipeline keys off isinstance(extractor, SummaryProvider)).
_NO_SUMMARY = ("barclays_2026", "monzobank", "metrobank")


@pytest.mark.parametrize("bank,sample", sorted(_SAMPLES.items()))
def test_known_extractor_sample_reconciles(
    bank: str, sample: str, samples_dir: Path
) -> None:
    extractor = _EXTRACTORS[bank]()
    assert isinstance(extractor, SummaryProvider), f"{bank} should expose summary()"
    pdf = samples_dir / sample
    rows = extractor.extract(pdf)
    txns = normalize.normalize(rows, pdf)
    raw_summary = extractor.summary(pdf)
    assert raw_summary is not None, f"{bank}: expected a parseable summary"
    rec = normalize.reconcile(txns, raw_summary, pdf)
    assert rec.ok, f"{bank} failed to reconcile: {rec.issues}"


@pytest.mark.parametrize("bank", _NO_SUMMARY)
def test_summaryless_extractors_skip_reconciliation(bank: str) -> None:
    """Banks without a usable summary must not implement SummaryProvider, so
    the pipeline skips reconciliation for them instead of flagging a file."""
    assert not isinstance(_EXTRACTORS[bank](), SummaryProvider)
