# PDF Bank Statement → Excel

Drop in your PDF bank statements, get back tidy Excel spreadsheets — one per statement, every time. The columns are always the same:

**Date · Description · Money Out · Money In · Balance**

Everything runs **on your own computer**. No files are uploaded anywhere, no subscription, no internet connection needed after setup.

> **Text-based PDFs only.** If a statement is a scanned image (a photo saved as PDF, or a printout scanned in), the tool will detect it and skip it rather than produce wrong numbers. Ask your bank for a "digital" or "text" version instead.

---

## Supported banks

HSBC · Barclays · Lloyds · Metro Bank · Monzo · NatWest · Revolut · Starling · Virgin Money · Zempler

Statements from other banks use a best-effort extractor — results are usually good but worth a quick check.

---

## Choose your setup

There are three ways to run the tool. Pick whichever feels most comfortable.

| | Option | Best for |
|---|---|---|
| ⭐ | **Browser app (Streamlit)** | Most people — easiest to use |
| 📦 | **Desktop app (.exe)** | Zero ongoing setup — just an icon to click |
| 🐳 | **Docker** | Teams sharing one machine or server |

---

## ⭐ Option 1 — Browser app (recommended for most people)

You open a page in your web browser, drag in PDFs, click Convert, and download the Excel files. No command line needed after the one-time setup.

### Step 1 — Install Python (one time only)

Download **Python 3.11** or later from the official site:
<https://www.python.org/downloads/>

Run the installer. **Important:** on the first screen, tick the box that says **"Add Python to PATH"** before clicking Install.

### Step 2 — Get the project folder

If someone shared a ZIP file with you: right-click it → Extract All → choose a location you'll remember (e.g. your Desktop).

You should now have a folder called `pdf-bank-to-excel`.

### Step 3 — Launch the app

**Windows:** open the `pdf-bank-to-excel` folder, then double-click **`start.bat`**.

**Mac:** open Terminal, drag the `pdf-bank-to-excel` folder into the Terminal window, press Enter, then type:
```
bash start.sh
```

A browser tab opens automatically showing the app. The first launch downloads a few small packages — this takes about 30 seconds. Every launch after that is instant.

### Step 4 — Convert your statements

1. Click **Browse files** (or drag PDFs straight onto the upload area).
2. Select one or more PDF bank statements.
3. Click **Convert to Excel**.
4. A download button appears for each statement — click to save.

That's it. You can close the browser tab when you're done, and press `Ctrl+C` (or close the black terminal window) to fully stop the app.

---

## 📦 Option 2 — Desktop app (no ongoing setup)

If you don't want to keep Python installed, ask your IT contact or developer to **build the executable once** for you. After that, you get a single file (`run_gui.exe` on Windows, or `run_gui` on Mac) that you can double-click like any other program.

**For the person doing the build:**

```bash
pip install pyinstaller
pip install -e .
pyinstaller --onefile --windowed --add-data "config.toml:." run_gui.py
# The finished file appears in the dist/ folder
```

Hand the file in `dist/` to the end user. They double-click it, pick their PDFs in a file browser, then pick where to save the Excel files. No terminal, no Python, no setup.

---

## 🐳 Option 3 — Docker (for shared / server use)

Docker runs the tool in an isolated container — good if multiple people share a machine, or if you want to run conversions on a server.

### Step 1 — Install Docker Desktop

<https://www.docker.com/products/docker-desktop/>

Run the installer with default options. Open Docker Desktop from the Start menu and wait until the system-tray icon says **"Docker Desktop is running"**.

### Step 2 — Open PowerShell in the project folder

```powershell
cd "$HOME\Desktop\pdf-bank-to-excel"   # adjust path as needed
```

### Step 3 — Drop PDFs into `input/`

Open the `input` folder inside the project and copy your PDFs there.

### Step 4 — Build (one time)

```powershell
docker compose build
```

### Step 5 — Convert

```powershell
docker compose run --rm converter
```

Converted files appear in `output/`. Files that couldn't be read appear in `failed/` with a `.log` explaining why.

---

## Folder guide

| Folder | What it's for |
|---|---|
| `input/` | Put your PDF statements here |
| `output/` | Your Excel files appear here |
| `failed/` | Statements that couldn't be converted, with a log file next to each |
| `logs/` | One log file per day — useful if you need to report a problem |

---

## Troubleshooting

**"Python was not found" (Windows)** — reinstall Python from <https://www.python.org/> and make sure to tick **"Add Python to PATH"** on the first screen of the installer.

**The browser tab opens but nothing happens** — wait 10 seconds and refresh. On the very first run, packages are still installing in the background.

**A PDF ended up in `failed/`** — open the matching `.log` file in Notepad. The most common reason is that the PDF is a scanned image rather than a digital statement. Ask your bank for a downloadable digital copy.

**"Permission denied" running `start.sh` on Mac** — run this once in Terminal:
```
chmod +x start.sh
```
Then try again.

**Something else went wrong** — take a screenshot of the error message and send it to whoever manages this tool for you.

---

## For developers

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the pipeline design and conventions, and [`CLAUDE.md`](CLAUDE.md) for LLM workflow rules.

```bash
# Install with dev tools
pip install -e ".[dev]"

# Run tests
pytest -q

# Type check
mypy src/

# Lint
ruff check src/ tests/

# Add a new bank: create src/statement_to_excel/extractors/<bank>.py
# implementing the Extractor protocol from extractors/base.py
```
