# How to start your first Claude Code session

This file tells *you* (Venkat) how to bootstrap the project with Claude Code
in a way that doesn't burn tokens. Once the project is bootstrapped, you can
delete this file.

---

## Step 1 — Set up the folder

You should already have:

```
pdf-bank-to-excel/
├── ARCHITECTURE.md
├── CLAUDE.md
└── KICKOFF_PROMPT.md   ← this file
```

In a terminal, `cd` into `pdf-bank-to-excel/` and run:

```
git init
```

This matters. Without git, you can't review what Claude Code has changed,
and you can't roll back when it goes wrong. Commit early and often.

## Step 2 — Launch Claude Code

In the same folder:

```
claude
```

Claude Code will read `CLAUDE.md` automatically.

## Step 3 — Paste this as your *first* message

Copy the block below (everything between the `===` lines) and paste it as
the very first message of the session.

```
===
Read ARCHITECTURE.md and CLAUDE.md in full before doing anything else.
Confirm you've read them by quoting one specific rule from CLAUDE.md and
naming the five pipeline stages from ARCHITECTURE.md.

Then propose a plan to bootstrap the project skeleton:

  - pyproject.toml with the dependencies named in ARCHITECTURE.md
    (pdfplumber, pdf2image, pytesseract, openpyxl) plus dev deps
    (pytest, mypy, ruff)
  - .gitignore covering input/, output/, failed/, logs/, plus the usual
    Python ignores
  - config.toml with sensible defaults
  - The empty module structure under src/statement_to_excel/ exactly as
    listed in ARCHITECTURE.md, with module-level docstrings explaining
    each module's role
  - tests/ folder with conftest.py and an empty samples/ directory
  - A README.md aimed at a human user (how to install, how to run, where
    to put PDFs)

Do NOT implement extractor logic or OCR yet. Skeleton only. Every Python
module should be importable and have a docstring; functions can be `pass`
or `raise NotImplementedError("see ARCHITECTURE.md")`.

Show me the plan. I'll approve it before you create files.
===
```

## Step 4 — When Claude shows the plan

Read it. If it looks right, say "go". If it skipped something or added
something out of scope, point that out specifically — don't say "looks bad,
try again" because that wastes a turn. Say "you skipped config.toml, add it"
or "drop the requirements.txt, we're using pyproject only".

## Step 5 — Once the skeleton exists

```
git add -A
git commit -m "init: project skeleton per ARCHITECTURE.md"
```

That's your safety net. From here on, do one bank at a time. Suggested
order of next prompts (one session each, run `/clear` between them):

### Session 2 — Models and pipeline glue

```
Implement src/statement_to_excel/models.py and pipeline.py per
ARCHITECTURE.md. Models are dataclasses (Transaction, RawRow, Statement).
pipeline.py has a single `run(config: Config) -> None` function that
orchestrates the stages but each stage call can stay as a stub for now.
Add tests for the model invariants (Decimal for money, frozen dataclass,
exactly one of money_in/money_out is set on a Transaction).
```

### Session 3 — Detect + ingest + a generic extractor on one sample PDF

Drop *one* real bank statement PDF into `tests/samples/` first (anonymise
it). Then:

```
@tests/samples/<your-sample>.pdf

Implement ingest.py, detect.py, and extractors/generic.py just well
enough to convert THIS specific PDF into a valid Excel file via
export.py. We'll generalise later. Use pdfplumber. Don't touch OCR yet.
End-to-end test: feed the sample to pipeline.run() and assert the
output xlsx has the expected number of rows and the balance chain
validates.
```

### Session 4+ — One bank at a time

```
@tests/samples/hsbc-jan2026.pdf

Add extractors/hsbc.py. Detection signature: the string "HSBC UK Bank
plc" appearing in extracted text. Use the existing generic extractor
as a reference. Add tests/test_extractors_hsbc.py with at least one
sample.
```

### Session N — OCR

Only after at least one text-based bank works end-to-end:

```
@tests/samples/barclays-scanned.pdf

Implement the OCR fallback per ARCHITECTURE.md. detect.py decides
text vs scanned via the per-page character threshold from config.toml.
ocr.py wraps pdf2image + pytesseract and returns text in the same
shape pdfplumber would have. Extractors stay unchanged. Add a test
that runs the scanned sample end-to-end.
```

## Token-saving habits while coding

- Use `/clear` whenever you switch tasks. Don't keep one giant session
  running for the whole project.
- Reference files with `@path/to/file.py` instead of pasting them.
- Press `Shift+Tab` to enter plan mode when you want a plan before edits.
- Commit between every working state. If Claude breaks something, `git
  reset --hard HEAD` is faster and cheaper than asking it to fix.
- Don't ask for explanations and edits in the same turn. Pick one.
- If a session feels like it's spinning ("let me try another approach…"
  three times in a row), stop, `/clear`, and rephrase the task with the
  specific file paths and the specific error you saw.

## When something goes wrong

Two patterns work better than the obvious "please fix it":

1. **Quote the failure verbatim.** Paste the actual error message or the
   actual diff that's wrong. Don't paraphrase.
2. **Constrain the fix.** "Fix this without touching `extractors/hsbc.py`"
   beats "fix this", because it stops Claude rewriting unrelated files.

Good luck.
