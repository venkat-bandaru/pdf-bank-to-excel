# CLAUDE.md — Rules for LLMs working on this repo

This file is read automatically by Claude Code at the start of every session.
It is also helpful to any other LLM (Codex, Gemini, etc.) being asked to
contribute to this codebase.

**Read `ARCHITECTURE.md` before making any non-trivial change.** That document
explains *why* the project is shaped the way it is. Do not violate it without
first asking the human to update `ARCHITECTURE.md` to reflect the new decision.

## What this project is

A local Python CLI that converts PDF bank statements in `input/` into Excel
files in `output/`, one `.xlsx` per PDF. Single-business use, no multi-tenant.
Standard accounting columns: Date, Description, Money Out, Money In, Balance.

## Conventions — style

- Python 3.11+. Type hints everywhere. `from __future__ import annotations`
  at the top of every module.
- `Decimal` for money. Never `float` for money.
- `datetime.date` for dates. Strings only at I/O boundaries.
- `dataclasses` (frozen where the value is immutable). No Pydantic.
- `pathlib.Path` for paths. No `os.path` string concatenation.
- `logging` for output. **No `print()`** in `src/`. `print()` is acceptable
  in throwaway scripts only.
- f-strings for formatting. No `%` or `.format()`.
- Docstrings on every public function and class. Use Google style. Explain
  *why*, not *what* — the code already says what.
- Module-level docstrings explain the module's role in the pipeline.

## Conventions — structure

- One extractor per bank, one file. Located in `src/statement_to_excel/extractors/`.
- New bank support = new file. Do not edit existing extractor files to handle
  another bank.
- Every extractor implements the `Extractor` protocol from
  `extractors/base.py`. If you change that protocol, update every extractor.
- Stage modules (`ingest.py`, `detect.py`, `extract` via extractors,
  `normalize.py`, `export.py`) are pure where possible: input in, output out,
  no global state.
- Configuration lives in `config.toml`. No env vars, no CLI flags for tunable
  values.

## Conventions — testing

- `pytest`. Every new extractor needs at least one test with a real (anonymised)
  sample PDF in `tests/samples/`.
- Don't commit real customer data. Anonymise account numbers, names, and
  reference fields in fixtures.
- Tests must be deterministic. Don't depend on the current date, locale, or
  filesystem ordering.
- Run the full test suite before declaring a task complete:
  `pytest -q`.

## Conventions — dependencies

- Add a dependency only if it earns its keep. Justify the addition in the
  PR description (or the chat, if there's no PR).
- License must be MIT, BSD, Apache 2.0, or PSF. No GPL/AGPL.
- Pin dependencies in `pyproject.toml` with `~=` (compatible release).

## Workflow rules for LLMs

- **Plan first, code second.** When given a task, propose the change as a
  short plan (files to touch, why, in what order) before editing.
- **Stay in scope.** Do not refactor unrelated code while implementing a
  feature. If you spot something worth fixing, note it; don't fix it.
- **Don't invent requirements.** If the user's request is ambiguous, ask.
  Do not pick a direction silently.
- **Ground claims in code.** When you say "this function does X", quote the
  line you read it from. When you say "this would break Y", point at Y.
- **Small commits.** One logical change per commit. Commit message format:
  `area: imperative subject` (e.g. `extractors/hsbc: handle multi-page tables`).
- **Don't add files outside the structure described in `ARCHITECTURE.md`.**
  If you think a new top-level folder is needed, propose it in `ARCHITECTURE.md`
  first.

## Workflow rules — efficiency

- Don't re-read files you've already seen this session.
- Prefer `rg` (ripgrep) over reading entire files when looking for symbols
  or strings.
- Do not run the full test suite after every micro-edit. Run it once at the
  end of a logical change.

## What "done" looks like for a typical task

1. Plan stated and accepted by the human.
2. Code change made.
3. Type checks pass: `mypy src/`.
4. Tests pass: `pytest -q`.
5. Linter passes: `ruff check src/ tests/`.
6. A short summary describing what changed and why, suitable for a commit
   message.

## When in doubt

Ask. The human prefers a clarifying question over a confident wrong turn.
