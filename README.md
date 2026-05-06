# PDF Bank Statement → Excel

Drop PDF bank statements into a folder, run one command, get tidy Excel
spreadsheets back. Every spreadsheet uses the same five columns:

**Date | Description | Money Out | Money In | Balance**

Runs entirely on your own machine — no cloud uploads, no subscription, no
API costs.

> Text-based PDFs only. If a statement is a scanned image (a photograph or
> a "save as PDF" of a printout), the tool will detect it and skip it
> rather than producing bad numbers.

---

## Quick start (Docker — recommended)

This is the easiest path. You install **one** program (Docker Desktop),
then run two commands. You don't need Python, and you don't need to know
how the tool works internally.

### 1. Install Docker Desktop

Download **Docker Desktop for Windows** from the official site:
<https://www.docker.com/products/docker-desktop/>

Run the installer with the default options. After it finishes, launch
Docker Desktop from the Start menu and wait until the whale icon in the
system tray (bottom-right of the taskbar) shows **"Docker Desktop is
running"**. The first launch can take a minute or two.

> macOS or Linux user? Same download page — pick the build for your OS.

### 2. Open the project in PowerShell

You should already have the project folder (`pdf-bank-to-excel`) on your
machine — either downloaded as a ZIP and extracted, or copied from a shared
drive.

Open **PowerShell** (Start menu → type "PowerShell" → press Enter), then
move into the project folder. For example, if the folder lives on your
desktop:

```powershell
cd "$HOME\Desktop\pdf-bank-to-excel"
```

Adjust the path to wherever the folder actually is on your machine.

### 3. Drop your PDFs into `input/`

Open the `input` folder inside the project (in File Explorer) and copy
your PDF bank statements into it. You can drop in as many as you like —
the tool processes them all in one go.

### 4. Build the image (one-time)

The first time you use the tool — and again any time the code is updated —
run:

```powershell
docker compose build
```

This downloads and prepares everything the tool needs. The first build can
take a few minutes; later runs are quick because Docker reuses what it
already has.

### 5. Convert

```powershell
docker compose run --rm converter
```

When the command finishes:

- Converted spreadsheets are in `output/` — one `.xlsx` per PDF.
- PDFs the tool couldn't read are in `failed/`, with a `.log` file next to
  each one explaining why.
- A run log for today lives in `logs/`.

You can repeat step 5 as often as you like. Drop new PDFs into `input/`,
run the command, collect the spreadsheets.

---

## Folder map

| Folder | What it's for |
|---|---|
| `input/` | PDFs you want to convert (you put them here) |
| `output/` | The generated `.xlsx` files (the tool puts them here) |
| `failed/` | PDFs the tool couldn't read, plus a `.log` for each |
| `logs/` | One log file per day, useful if something goes wrong |

These folders are shared with the Docker container, so anything written
inside the container appears immediately in the matching folder on your
machine.

---

## Configuration

Open `config.toml` in any text editor (Notepad works fine). The settings
are commented inline. The next run picks up your changes — no rebuild
needed.

---

## Supported banks

The tool ships with dedicated extractors for these UK banks:

HSBC · Barclays · Lloyds · Metro Bank · Monzo · NatWest · Revolut ·
Starling · Virgin Money · Zempler

Statements from other banks fall through to a best-effort generic
extractor. Results are usually usable but worth a quick visual check.

---

## Troubleshooting

**"docker: command not found"** — Docker Desktop isn't installed, or it
isn't running. Open Docker Desktop from the Start menu and wait for the
"running" status.

**"Cannot connect to the Docker daemon"** — Docker Desktop is installed
but not started. Launch it and try again.

**A PDF ended up in `failed/`** — open the matching `.log` file. The most
common reasons are: the PDF is a scanned image (not text), or the layout
doesn't match any of the supported banks closely enough for the generic
extractor.

**Nothing happens when I run the command** — make sure you're in the
project folder (the one that contains `docker-compose.yml`). Run `dir` in
PowerShell to check.

---

## Alternative: run without Docker (developers only)

You don't need this section unless you want to modify the code. For just
processing PDFs, the Docker path above is simpler.

### Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.11+ |
| Git | latest |

### Setup

```powershell
# 1. Clone the repo (or unzip a copy into a folder)
git clone <repo-url>
cd pdf-bank-to-excel

# 2. Create an isolated Python environment named "venv"
python -m venv venv

# 3. Activate it
#    Windows (PowerShell):
.\venv\Scripts\Activate.ps1
#    macOS / Linux:
source venv/bin/activate

# 4. Install the package together with dev tools
pip install -e ".[dev]"
```

> If PowerShell refuses to run `Activate.ps1` because of an execution
> policy, run this once in the same PowerShell window first:
> `Set-ExecutionPolicy -Scope Process Bypass`

### Run

```powershell
python -m statement_to_excel
```

### Tests, type checks, lint

```powershell
pytest -q
mypy src/
ruff check src/ tests/
```

---

## Adding a new bank

Add a new file under `src/statement_to_excel/extractors/` that implements
the `Extractor` protocol from `extractors/base.py`. See `ARCHITECTURE.md`
for the full design and conventions.
