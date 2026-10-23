# tidyup - a safe file organizer for Windows

tidyup sorts the files in your Documents, Desktop, Downloads, Pictures, Videos and OneDrive folders into a tidy structure. It never deletes anything, it shows you every planned change in a spreadsheet before it does anything, and every run can be undone.

You run it yourself, when you want. **There is no scheduled or background task.**

Contents: [Install](#1-install) | [Settings](#2-edit-configyaml) | [First run](#3-first-run-walkthrough) | [Moving to D:](#4-moving-your-files-to-the-d-drive) | [Upkeep](#5-quick-tidy-with-tidyup-upkeep) | [Commands](#6-command-reference) | [Safety](#7-safety-what-tidyup-will-never-do) | [Defaults](#8-defaults-we-chose) | [Troubleshooting](#9-troubleshooting) | [Claude Code](#10-let-claude-code-run-it-for-you)

---

## 1. Install

You need Windows 10 or 11 and Python 3.11 or newer.

### 1.1 Install Python

1. Go to <https://www.python.org/downloads/> and download the latest Python 3 installer for Windows.
2. Run it. On the **first screen, tick "Add python.exe to PATH"** (at the bottom). This is easy to miss and important.
3. Click "Install Now".
4. Check it worked: press the Windows key, type `cmd`, press Enter, and type `python --version`. You should see `Python 3.11` or higher.

### 1.2 Download tidyup

Option A - ZIP (easiest): on the GitHub page of this project click the green **Code** button, then **Download ZIP**. Right-click the ZIP, choose **Extract All**, and put the folder somewhere simple, for example `C:\tidyup`.

Option B - git (if you have it installed): open a terminal and run

```
git clone https://github.com/miklHwang/fileOrganizer C:\tidyup
```

### 1.3 Open a terminal in that folder

Open the folder in File Explorer, click the address bar, type `cmd` (or `powershell`) and press Enter. A terminal opens already in the right folder.

### 1.4 Create a virtual environment (once)

A virtual environment is a private box for tidyup's add-ons, so it does not mix with anything else on your PC.

```
python -m venv .venv
```

### 1.5 Activate it (every time you open a new terminal)

In **cmd**:

```
.venv\Scripts\activate.bat
```

In **PowerShell**:

```
.venv\Scripts\Activate.ps1
```

If PowerShell says "running scripts is disabled on this system", run this once and then try again:

```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

(Answer `Y` if asked. It only affects your own user account.)

When it works you will see `(.venv)` at the start of the line.

### 1.6 Install the add-ons

```
pip install -r requirements.txt
```

Check that tidyup runs:

```
python -m tidyup --version
python -m tidyup --help
```

All commands in this guide are typed as `python -m tidyup <command>` from the tidyup folder, with the virtual environment activated.

---

## 2. Edit config.yaml

`config.yaml` in the tidyup folder holds all settings. Open it with Notepad. Every setting has a plain-language comment. Lines starting with `#` are notes and are ignored. Keep the indentation as it is; if you break something, tidyup tells you which setting is wrong.

By default tidyup finds your folders itself (including when OneDrive has taken over Documents, Desktop or Pictures), looks through Documents, Desktop, Downloads, Pictures, Videos and OneDrive, and organizes files **in place**. The things people most often change:

- `destination: root:` - set to e.g. `'D:\Organized'` to collect everything under one folder instead of organizing in place. Use single quotes around Windows paths.
- `delete_mode:` - `review` (default) or `recycle_bin`. See [Safety](#7-safety-what-tidyup-will-never-do).
- `exclusions:` - file names and full paths tidyup must ignore.
- `atomic:` - folders that are moved as one unit and never reorganized inside (programming projects, app data).
- `categories:` - the document categories and the words that identify them. Add your own words.
- `rules: pdf: date_order:` - `MDY` (default, month first) or `DMY` (day first) for dates like 03/04/2024.
- `csv: delimiter:` - use `";"` if your Excel uses semicolons.

You can keep several config files and choose one with `--config path\to\other.yaml`.

---

## 3. First run walkthrough

Take these steps in order. Nothing is moved until step 4.

### Step 1 - scan (read-only)

```
python -m tidyup scan
```

Lists your files into `tidyup_data\inventory.csv` and prints a summary. It changes nothing.

### Step 2 - plan

```
python -m tidyup plan
```

Decides where each file should go and writes `tidyup_data\manifest.csv`. Still nothing is moved. If a proposed move would take a file **out of OneDrive**, a warning is printed (and that row is set to `N`).

### Step 3 - review `manifest.csv` and `report.html`

Build the report and open it in your browser:

```
python -m tidyup report --open
```

`report.html` shows summary counts, the proposed folder tree, a thumbnail grid of detected screenshots, duplicate groups side by side, look-alike photos, likely old versions, and OneDrive warnings.

Then open `tidyup_data\manifest.csv` in Excel. Each row is one proposed action:

| Column | Meaning |
|---|---|
| id | Row number |
| action | move, rename, move_rename, review_delete, duplicate, or skip |
| source / destination | Where the file is now / where it would go |
| rule | Which rule decided this |
| confidence | high, medium or low |
| notes | The reasons, and any warnings |
| approved | **Y** = will be done, **N** = will not be done |

High-confidence rows start as `Y`; medium and low start as `N`. Change `approved` to `Y` or `N` as you like. Then **save as CSV** (File > Save As > "CSV UTF-8 (Comma delimited)" or plain "CSV"; keep the same name and place). Excel's "Keep using that format" prompt: choose Yes. tidyup accepts Excel's re-saves, including semicolon separators and `yes`/`x`/`1` for Y.

Close the file in Excel before the next step.

### Step 4 - apply to Downloads only, 50 files

```
python -m tidyup apply --only-folder downloads --limit 50
```

tidyup lists what it is about to do and asks you to confirm. Type `y` to continue. Only approved rows are done. At the end it prints a **run id** (a timestamp) you can use to undo.

### Step 5 - check the results

Look in your Downloads folder in File Explorer: files should be in `Installers`, `Pictures\...` and so on. Archives (`.zip`, `.7z`, ...) stay exactly where they were. Check that names, dates and contents look right. To see what was done: `python -m tidyup undo --list`.

### Step 6 - test undo

```
python -m tidyup undo --dry-run
python -m tidyup undo
```

The first line shows what would be reversed; the second does it (latest run by default; or `undo <run id>`). Check that your files are back exactly where they were. Seeing this work once is the best way to trust the tool.

### Step 7 - full run

Happy? Run the real thing. Optionally preview first:

```
python -m tidyup apply --dry-run
python -m tidyup apply
```

If you changed files after planning, re-run `scan` and `plan` first (this rewrites `manifest.csv`, so your Y/N choices are lost - make a copy of the old one if you want to keep them). You can also split a run: `--only-folder NAME`, `--only-rule NAME`, `--limit N`, or `--manifest PATH` for a manifest in another place.

---

## 4. Moving your files to the D: drive

Do this **after** the cleanup, when you want your organized folders on a bigger drive.

1. Preview (moves nothing; writes `tidyup_data\migrate_manifest.csv`):

   ```
   python -m tidyup migrate --to D:\ --dry-run
   ```

   It also checks free space on D: and tells you if there is not enough. The real run refuses to start without enough space (plus a safety margin, 1024 MB by default).
2. Open `migrate_manifest.csv` in Excel and review it (set a row's `approved` to `N` to leave it behind; save as CSV).
3. Run it:

   ```
   python -m tidyup migrate --to D:\
   ```

   Each file is copied, **checked with a SHA-256 fingerprint, and only then** is the original removed. Timestamps are preserved. Your folders land in `D:\Documents`, `D:\Pictures`, and so on.
4. **If it is interrupted** (power cut, you pressed Ctrl+C, a locked file), just run the same command again. It continues where it stopped.
5. `python -m tidyup undo` can reverse a migration.
6. Finally, tell Windows about the new location (tidyup does **not** change this for you): in File Explorer right-click **Documents** (or Pictures, Videos, Downloads) > **Properties** > **Location** tab > **Move...** > pick the folder on D: > OK. When asked to move files, choose **No** (they are already there).

By default the OneDrive folder is not migrated (`migrate: include_onedrive: false`), and online-only files are never moved across drives. The list of folders is in `config.yaml` under `migrate:`.

---

## 5. Quick tidy with `tidyup upkeep`

`upkeep` is a quick tidy of **Downloads, Desktop and your Screenshots folder**. Run it only when needed, whenever Downloads or your Desktop get cluttered. Nothing runs by itself; there is no scheduled task.

What it does:

- Looks only at the folders in `upkeep: sources:` (default Downloads, Desktop, Screenshots).
- Only does `move`, `rename` and `move_rename` actions, and only those tidyup is **highly confident** about (e.g. an installer to `Installers`, a clear screenshot to `Screenshots_Review`, a dated photo to its month folder).
- Leaves archives (`.zip`, `.7z`, ...) alone; they are never moved.
- Shows you what it will do and **asks once** before moving anything. Add `--yes` to skip the question.
- **Never** deletes, never sends to review/delete, never touches anything uncertain; those stay for a full `scan` / `plan` / review round.
- Is logged like any other run, so `tidyup undo` reverses it.

```
python -m tidyup upkeep --dry-run     (show only, change nothing)
python -m tidyup upkeep               (asks before moving)
python -m tidyup upkeep --yes         (no question)
```

---

## 6. Command reference

Run `python -m tidyup --help` or `python -m tidyup <command> --help` for the same information.

Options that work with every command (you can type them before or after the command name):

| Option | Meaning |
|---|---|
| `--config PATH` | Use this config file (default: `config.yaml` in the current folder) |
| `--dry-run` | Show what would happen without changing anything |
| `--debug` | Show full error details (for troubleshooting) |
| `--version` | Show the version (put before the command) |
| `-h`, `--help` | Show help |

| Command | What it does | Extra options |
|---|---|---|
| `scan` | Read-only: lists your files into `inventory.csv` | - |
| `plan` | Decides where files should go; writes `manifest.csv` | - |
| `report` | Builds `report.html` | `--open` opens it in your browser |
| `apply` | Carries out the approved rows of the manifest | `--manifest PATH`, `--limit N`, `--only-rule NAME`, `--only-folder NAME`, `--yes` |
| `undo [run_id]` | Reverses a run (default: the latest) | `--list` shows runs that can be undone |
| `migrate` | Moves organized files to another drive | `--to PATH` (required), `--manifest PATH`, `--yes` |
| `upkeep` | Quick on-demand tidy of Downloads, Desktop, Screenshots | `--yes` |

`--dry-run` behaviour: `scan`, `plan` and `report` compute and print but write nothing; `apply`, `undo` and `upkeep` print the planned operations; `migrate --dry-run` writes only `migrate_manifest.csv`.

`apply`, `migrate` and `upkeep` ask for confirmation unless `--yes` is given; if there is no keyboard to answer (for example in a script) and no `--yes`, they refuse. Exit codes: 0 ok, 1 error or refused, 2 bad arguments, 130 interrupted.

Files tidyup keeps in `tidyup_data\` (next to `config.yaml`): `inventory.csv`, `manifest.csv`, `migrate_manifest.csv`, `report.html`, and `logs\` (the undo logs, plus a `crash_*.txt` if something unexpected happens). This folder is never scanned.

---

## 7. Safety - what tidyup will never do

1. **It never deletes a file.** "Delete" means moving it to the `_Review_Delete` folder, where you decide. The only removals are of its own temporary copies, of empty folders it created itself, and of an original after a cross-drive copy has been verified.
2. **It never touches system areas**: `C:\Windows`, Program Files, ProgramData, AppData, `$Recycle.Bin`, System Volume Information. It also never touches **hidden or system files**, and **any file or folder whose name starts with a dot** (like `.gitignore`) is treated as hidden and left alone.
3. **It never reorganizes inside a project or app folder.** A folder with `.git`, `package.json`, `*.sln`, `*.csproj`, `pyproject.toml`, `node_modules` or `venv` inside, or a known app folder ("My Games", "Outlook Files", "OneNote Notebooks", "Zoom", "Visual Studio*", "Adobe", ...), is moved as one whole unit or left alone. The lists are in `config.yaml`.
4. **It never changes anything you have not approved.** Everything goes through a manifest you can read, and only rows marked `Y` are done. (`upkeep` is the one shortcut: only high-confidence moves and renames, after one question.) A real `migrate` needs a reviewed dry-run manifest first.
5. **Every change is written to an undo log**, and `undo` reverses a whole run, including folders it created.
6. **It never overwrites a file.** If the name is taken it adds ` (2)`, ` (3)`, and so on.
7. **It keeps file dates** (created/modified) when moving or copying.
8. **It never removes a file after a cross-drive move until the copy is verified** with SHA-256 and the original is unchanged.
9. **Long paths (over 260 characters) are supported.**
10. **Locked or in-use files are skipped**, noted in the log, and the run carries on.
11. **It never opens online-only OneDrive files.** Files that exist only in the cloud are never read (no fingerprinting, PDF text or photo data), because that would force a download. They are sorted by name, type, size and dates only and marked "cloud-only". They are also never moved out of OneDrive or to another drive.
12. **It warns you before moving anything out of OneDrive**, since that removes it from the cloud; such rows are set to `N`. By default files in OneDrive stay in OneDrive.
13. **`upkeep` never deletes or flags for deletion**; it only does high-confidence moves and renames in Downloads, Desktop and Screenshots.
14. **`migrate` refuses to start if the target drive does not have enough free space.**
15. It does not follow shortcuts-to-folders (symlinks and junctions), and does not move them.
16. It never runs on its own: no scheduled task, no background service.

**Recycle Bin limitation.** If you set `delete_mode: recycle_bin`, "delete" sends the file to the Windows Recycle Bin instead of `_Review_Delete`. `tidyup undo` **cannot** take files back out of the Recycle Bin; it lists them so you can restore them yourself (open the Recycle Bin, right-click > Restore). The default, `review`, is fully undoable.

---

## 8. Defaults we chose

Where the original request did not say, these are the choices made. Most can be changed in `config.yaml`.

1. **In-place layout**: `Documents/<Category>` goes into your real Documents folder; `Pictures/...` and `Videos/...` into your real Pictures and Videos; `Installers` inside Downloads (`Archives` only if you set `rules: archives: keep_in_place: false`); `_Unsorted`, `_Duplicates` and `_Review_Delete` in your user folder (e.g. `C:\Users\me\_Unsorted`). Files already in OneDrive stay in OneDrive (same structure under the OneDrive folder).
2. **Work files** (`inventory.csv`, `manifest.csv`, `report.html`, `logs\`) live in `tidyup_data\` next to `config.yaml` and are never scanned.
3. **Atomic folders** are listed as one item and left in place when organizing in place; with a destination root they move whole (medium confidence, approved N). Your source folders themselves are never atomic. Extra markers: `.venv`, `pyvenv.cfg`.
4. **Confidence**: photos with a camera date or a date in the file name: high; modified date only: medium. Videos with a date in the name: high; otherwise medium. Installers in Downloads: high. Archives are left in their folder (a `skip` row). Installers older than 90 days: suggested for `_Review_Delete`, low, never auto-approved. Exact duplicates: high (approved Y). Documents are moved, never renamed: Word files with a strong text match are high, a file-name match is medium. Screenshots: high at score 5 or more, medium at 3-4.
5. **Scanned PDFs** (no readable text) with a keyword in the file name go to `Documents/<Category>` with their original name (medium); otherwise to `_Unsorted/PDFs`. Text PDFs are renamed `YYYY-MM-DD_Category_Short-Description.pdf` only when confidence is high.
6. **Unsorted** files are split by type: `_Unsorted/Images`, `Videos`, `Audio`, `PDFs`, `Documents`, `Installers`, `Other` (archives are never moved, see 13).
7. **Files already in the target structure** are left alone unless a high-confidence rule applies.
8. **Version chains** ("report v3", "final_FINAL", "(1)", "copy of"): only the older files are forced to `N`; the newest keeps its normal default. They are listed in the report as "likely old versions".
9. **Which duplicate to keep**: the best location (Documents, then Pictures, Videos, OneDrive, Desktop, Downloads), then a clean name, then the oldest created date, then the shortest path. Empty files are never treated as duplicates. Others go to `_Duplicates` with a note naming the kept copy.
10. **Look-alike photos** (perceptual hash distance of 6 or less) are only shown in the report, never moved.
11. **Dates in PDFs** are read as US order (MM/DD/YYYY); set `rules: pdf: date_order: DMY` to change. The first 2 pages are read.
12. **Long-path prefix** is applied when a path reaches 240 characters.
13. **Archives stay where they are**: `.zip .7z .rar .tar .gz .tgz .bz2 .xz .iso` files are never moved, renamed or sent to `_Duplicates`; they show as `skip` rows. Set `rules: archives: keep_in_place: false` in `config.yaml` to collect Downloads archives into `Downloads\Archives` instead.
14. **CSV files** are UTF-8 with BOM and comma-separated (configurable), so Excel opens them cleanly.
15. **Moves out of OneDrive** are always approved `N` with a WARNING note.
16. **Recycle Bin mode** is allowed but not undoable (see above). The default is the review folder.
17. **Migrate**: folders Documents, Pictures, Videos, Downloads plus `_Unsorted`, `_Duplicates`, `_Review_Delete`; target `D:\<FolderName>`; OneDrive skipped; 1024 MB free-space margin; a dry run must create `migrate_manifest.csv` first; empty source folders are left behind; Windows folder locations are not changed automatically.
18. **No scheduled task.** `tidyup upkeep` is the on-demand quick tidy; it asks once before moving anything unless `--yes`.
19. **Screenshot detection** adds up points: name starting with "screenshot", "screen shot", "capture" or "snip"; being in the Screenshots folder; PNG; no camera data; a size matching a common screen; a phone-like shape. Camera data counts against. The reasons are written in `notes`.
20. Photo file types: jpg, jpeg, heic, heif, dng, cr2, nef, arw, tif, tiff. Video types: mp4, mov, avi, mkv, wmv, m4v, 3gp, webm, mts, m2ts. Excluded by default: `desktop.ini`, `thumbs.db`, `~$*`, `*.tmp`, `*.crdownload`, `*.part`, `*.partial`, `*.lnk`, `*.url`.

---

## 9. Troubleshooting

**"python is not recognized"** - Python was installed without "Add python.exe to PATH". Run the Python installer again, choose Modify/Repair, or reinstall and tick the box. Then open a new terminal.

**PowerShell: "running scripts is disabled"** - run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use cmd instead.

**"No module named ..."** - the virtual environment is not active (no `(.venv)` at the start of the line). Activate it (step 1.5), then run `pip install -r requirements.txt` again.

**A message about the config file** - tidyup names the setting that is wrong. Check the indentation and quotes in `config.yaml`. Windows paths need single quotes: `'D:\Organized'`. If unsure, compare with a fresh copy of the file.

**"File is locked" / skipped files** - another program has the file open (Word, a browser, a sync client). tidyup skips it, notes it in the log, and carries on. Close the program and run `scan` and `plan` again.

**Excel will not let me save / tidyup cannot read the manifest** - close the file in Excel first. Save as CSV, not as .xlsx. If your Excel uses semicolons, either leave it (tidyup reads both) or set `csv: delimiter: ";"`.

**OneDrive warnings** - a row marked WARNING would move a file out of OneDrive, which removes it from the cloud and from your other devices. Those rows are `N` by default; leave them `N` unless you really want that. Files shown as "cloud-only" are online-only: tidyup does not open them, so they are only sorted by name and type, and many end up in `_Unsorted` or stay put. To sort them by content, right-click the file in OneDrive > "Always keep on this device" first, then scan again.

**Very long paths** - tidyup handles paths over 260 characters itself. If another program (including Excel or Explorer) cannot open such a file, shorten the folder names or move the folder closer to the drive root.

**I want to go back** - `python -m tidyup undo --list`, then `python -m tidyup undo` (latest) or `python -m tidyup undo <run id>`. If some items cannot be undone, tidyup says so and does not mark the run as undone; fix the problem and run the same undo again.

**Something unexpected happened** - run the command again with `--debug` for full details. Crash reports are saved in `tidyup_data\logs\`.

**A file I expected to be sorted was left alone** - it may be hidden, excluded in `config.yaml`, inside an atomic folder, already in the right place, or in a folder that is not in `sources`.

---

## 10. Let Claude Code run it for you

If you use Claude Code, it can run tidyup for you and explain each step. It has to be Claude Code on **this Windows PC** (the desktop app or the terminal). A cloud session can't see your files.

1. Get tidyup with git. Claude Code on Windows needs Git for Windows anyway.

   ```
   git clone https://github.com/miklHwang/fileOrganizer C:\tidyup
   ```

   To get updates later: `git -C C:\tidyup pull`.
2. Start Claude Code in that folder. In a terminal: `cd C:\tidyup`, then `claude`. In the desktop app, pick `C:\tidyup` as the folder.
3. Type `/tidyup`, or just ask, for example "tidy up my Downloads" or "move my organized folders to D:".

Claude Code follows `.claude/skills/tidyup/SKILL.md`:

- It sets up the virtual environment if needed.
- It previews each step with `--dry-run` and explains it.
- It asks you before anything is moved, every time.
- It never moves or deletes files itself. Only tidyup does, and every run can be undone.

Claude Code also asks before running any command. The read-only ones (`scan`, `report`, `undo --list` and previews) are already allowed in `.claude/settings.json`.
