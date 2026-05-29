# Architecture — PDF Bank Statement → Excel

This document explains *why* the project is built the way it is, so a human or
an LLM joining the project can make consistent decisions without re-deriving
them.

## Goal

Take PDF bank statements dropped into an `input/` folder and emit a matching
`.xlsx` file per PDF into `output/`, using a fixed accounting schema:
`Date | Description | Money Out | Money In | Balance`.

This is a single-business tool. There is no concept of "client" or "tenant".

## Non-goals

- Categorising transactions (no rules engine, no GL coding).
- Reconciling against another ledger.
- A web UI or service. This is a local CLI / script.
- OCR / scanned-PDF support (text-only PDFs are in scope; see "Scanned PDFs"
  below).

## Pipeline

The tool is a linear pipeline. Every stage has one job and a typed input/output,
so it can be tested in isolation and replaced without touching the others.

```
input/*.pdf
   │
   ▼
[1] ingest      — discover PDFs, decide what to process
   │
   ▼
[2] detect      — identify the bank (HSBC / Barclays / generic)
   │              and whether the PDF is text or scanned
   │
   ▼
[3] extract     — pull raw rows out of the PDF using pdfplumber
   │              one extractor module per bank layout
   │
   ▼
[4] normalize   — coerce raw rows into the canonical Transaction model
   │              (parse dates, money, drop rubbish, validate balance chain)
   │
   ▼
[5] export      — write a .xlsx to output/ with the standard columns
   │
   ▼
output/*.xlsx
```

If any stage fails for a given PDF, the file is moved to `failed/` with a
sibling `.log` explaining why. The rest of the batch keeps going.

## Why a strategy pattern for extractors

Bank statements look wildly different across banks. HSBC's tables, Barclays'
tables, and a random building society's tables share almost no structure.
Trying to write one extractor that handles all of them produces a mess of
`if bank == "hsbc"` branches.

Instead: one module per bank *layout* under `extractors/`, each exposing
the same `Extractor` protocol (`extract(pdf_path) -> list[RawRow]`). A
dispatcher in `detect.py` picks the right extractor based on text
fingerprints in the PDF ("HSBC UK Bank plc", "Barclays Bank UK PLC",
etc.). A `generic.py` fallback uses heuristics for unknown layouts —
best-effort, expected to fail sometimes, that's fine.

To support a new bank, you add one file. You do not modify existing files.

When the same bank rolls out a new statement format that shares no
parsing code with the old one, treat it as a new layout: add a sibling
file (e.g. `barclays_2026.py` next to `barclays.py`) and a more-specific
fingerprint in `detect.py` that runs *before* the legacy bank's
fingerprint. The legacy extractor stays untouched so existing customer
statements keep working.

## Scanned PDFs

Some banks issue scanned PDFs where the "text" is actually an image.
`pdfplumber` returns nothing useful for these.

Detection heuristic: if `pdfplumber` extracts fewer than N characters per page
on average (`detect.min_chars_per_page` in `config.toml`), `detect.py`
classifies the PDF as `scanned` and logs the classification.

We do not OCR scanned PDFs. Earlier iterations sketched an OCR path
(`pdf2image` + `pytesseract`); it was descoped because the sample set we
actually need to handle is text-based, and local OCR added install friction
(Tesseract, Poppler) and accuracy problems (1 vs l, 0 vs O misreads) without
a clear payoff. Scanned PDFs flow through the pipeline and produce an empty
`.xlsx`; the log line `kind=scanned` is the signal that no OCR happened.

If scanned-PDF support is needed later, add an OCR module and wire it into
`pipeline.py` between `detect` and `extract`. Update this section accordingly.

The `normalize` stage still validates that running balances chain correctly
(`prev_balance + money_in - money_out == balance`); rows that break the chain
are flagged in the Excel output so a human can spot-check.

## Statement-level reconciliation

The per-row balance-chain check above is weak for layouts that print a running
balance only once per day (HSBC, for example): the intermediate rows have no
balance to diff against, so a *whole missing transaction* on a multi-row day
slips through without flagging anything.

To catch that, an extractor may also expose the statement's printed
"Account Summary" totals (opening balance, total paid in, total paid out,
closing balance) via the optional `SummaryProvider` protocol in
`extractors/base.py`. This is deliberately separate from the `Extractor`
protocol: not every layout prints usable totals, and making it optional means
adding it to one extractor does not change the contract for the others. The
pipeline checks `isinstance(extractor, SummaryProvider)` and, when present,
calls `normalize.reconcile()` to assert three things end-to-end:

- `sum(money_in)  == stated "Payments In"`
- `sum(money_out) == stated "Payments Out"`
- `opening + in - out == stated closing balance`

The result is a `Reconciliation` on the `Statement`. A mismatch is logged at
WARNING and written to a "Summary" worksheet in the output `.xlsx` (PASS /
REVIEW plus the specific discrepancies). A failed reconciliation does **not**
move the file to `failed/`: the rows we did extract are still useful, and the
accountant wants to see them alongside the flag, not have them hidden.

## Canonical model

`models.py` defines `Transaction` as a frozen dataclass:

```python
@dataclass(frozen=True)
class Transaction:
    date: datetime.date
    description: str
    money_out: Decimal | None   # exactly one of money_out / money_in is set
    money_in: Decimal | None
    balance: Decimal | None     # may be None if statement omits it
    confidence: Literal["ok", "low"]  # "low" if balance chain didn't validate
```

Money is `Decimal`, never `float`. Dates are `datetime.date`, never strings.
This is enforced at the `normalize` boundary — extractors return `RawRow`
(strings), and only `normalize` produces `Transaction`.

## Configuration

A single `config.toml` in the project root holds tunable values:

- `input_dir`, `output_dir`, `failed_dir`
- `detect.min_chars_per_page` — threshold for "this is scanned"
- `extractors.priority` — list, controls dispatcher order

No environment variables, no flags. One file, one source of truth.

## Logging

`logging` from the stdlib, configured in `__main__.py`. Every PDF gets a log
line per stage at INFO. Failures are ERROR with a traceback. Logs go to
stdout *and* `logs/run-YYYY-MM-DD.log`. No `print()` anywhere in the codebase.

## Testing

`pytest`. Tests live under `tests/`. Sample PDFs (anonymised — real account
numbers and names redacted) live under `tests/samples/`.

Each extractor has its own test file with at least one good sample and one
known-tricky sample. The pipeline has an end-to-end test that runs the full
flow on a fixture and diffs the resulting `.xlsx` against an expected one.

## Folder layout

```
pdf-bank-to-excel/
├── ARCHITECTURE.md          ← this file
├── CLAUDE.md                ← rules for LLMs working on this repo
├── README.md                ← human onboarding
├── pyproject.toml
├── config.toml
├── input/                   ← drop PDFs here (gitignored)
├── output/                  ← generated .xlsx (gitignored)
├── failed/                  ← unprocessable PDFs + .log files (gitignored)
├── logs/                    ← run logs (gitignored)
├── src/
│   └── statement_to_excel/
│       ├── __init__.py
│       ├── __main__.py      ← CLI entry: python -m statement_to_excel
│       ├── pipeline.py      ← orchestrates the stages
│       ├── models.py        ← Transaction, Statement, RawRow
│       ├── ingest.py
│       ├── detect.py
│       ├── normalize.py
│       ├── export.py
│       └── extractors/
│           ├── __init__.py
│           ├── base.py      ← Extractor protocol
│           ├── hsbc.py
│           ├── barclays.py
│           └── generic.py
└── tests/
    ├── conftest.py
    ├── samples/
    └── test_*.py
```

## Library choices

- **pdfplumber** — best balance of table extraction quality and ergonomics
  for text PDFs. We tried `pypdf` (too low-level) and `pymupdf` (excellent
  but AGPL-licensed, which complicates redistribution).
- **openpyxl** — for `.xlsx` output. Standard choice, no surprises.
- **dataclasses** (stdlib) — over Pydantic. We don't need runtime validation
  at every boundary; the only place validation matters is `normalize`, and
  it's clearer as explicit code than as decorators.
- **tomllib** (stdlib, Python 3.11+) — for `config.toml`. No extra dep.

## Out of scope, deliberately

Things that look tempting but we are *not* adding until there is a concrete
need:

- A plugin system for extractors (just add a file).
- Async / parallel processing (one PDF at a time is fast enough; sequential
  is easier to debug).
- A database (CSV / Excel is the deliverable; persistence is the user's job).
- ML-based extraction (per-bank rules are good enough for known layouts;
  ML adds opacity and model-management overhead).
