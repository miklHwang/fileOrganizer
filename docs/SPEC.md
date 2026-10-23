AMENDMENT (2026-10-23, at the user's request): tidyup runs on an as-needed basis only. There is no scheduled/daily task; the `schedule` command is dropped and upkeep is an on-demand command the user runs when they want.

Build "tidyup", a Python command-line file organizer for my Windows 10/11 PC. I'll clone this repo and run it locally. The build happens in a Linux cloud environment, so all Windows-specific code must sit behind small functions that tests can mock. Builders make sensible defaults rather than stopping to ask, and note them in the README.

GOALS
1. One-time cleanup of my personal files: Documents, Desktop, Downloads, Pictures, Videos, and my OneDrive folder.
2. Ongoing upkeep, as needed: a lightweight on-demand run (`tidyup upkeep`) that keeps Downloads, Desktop and the Screenshots folder tidy. No scheduled or daily task.
3. Migration: later I'll move my organized files from C: to my D: drive, which has more space.

HARD SAFETY RULES (non-negotiable)
- NEVER hard-delete anything. A "delete" means moving the file to _Review_Delete/ (or the Recycle Bin via send2trash, if I choose that in config).
- NEVER touch: C:\Windows, Program Files*, ProgramData, AppData, $Recycle.Bin, System Volume Information, or any hidden/system files.
- Treat these folders as atomic units: move them whole and never reorganize inside them. That means any folder containing .git, package.json, *.sln, *.csproj, pyproject.toml, node_modules or venv, plus app-owned folders inside Documents (e.g. "My Games", "Outlook Files", "OneNote Notebooks", "Zoom", "Visual Studio*", "Adobe", "Custom Office Templates"). Make this list configurable.
- Every change goes through a manifest I can review. apply only executes rows marked approved.
- Every applied action is written to an undo log, and undo can fully reverse any run.
- Never overwrite a file. On a name collision, append " (2)", " (3)", etc.
- Preserve timestamps (shutil.copy2 semantics).
- Any cross-drive move is copy → verify with SHA-256 → only then remove the source.
- Support long paths (> 260 chars) using the \\?\ prefix on Windows.
- Skip locked or in-use files gracefully, log them, and continue.

ONEDRIVE HANDLING
- Detect online-only (cloud placeholder) files via st_file_attributes: FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS (0x400000), FILE_ATTRIBUTE_RECALL_ON_OPEN (0x40000), FILE_ATTRIBUTE_OFFLINE (0x1000).
- NEVER read the contents of a placeholder file (no hashing, no PDF text, no EXIF), because that forces a download. Classify these files by name, extension, size and dates only, and mark them "cloud-only" in the manifest.
- Warn clearly in plan output if a proposed move would take a file OUT of the OneDrive folder, since that removes it from the cloud.

COMMANDS
- tidyup scan — read-only. Walks the configured source folders and writes inventory.csv: path, size, extension, created/modified dates, is_cloud_only, sha256 (local files only; compute hashes only for files whose size matches another file's), image info, and atomic-folder membership. Shows a progress bar and a summary (counts and sizes by type and folder).
- tidyup plan — applies the rules to the inventory and writes manifest.csv with columns: id, action (move | rename | move_rename | review_delete | duplicate | skip), source, destination, rule, confidence (high/medium/low), notes, approved (Y/N). High confidence defaults to Y; medium and low default to N. Must open cleanly in Excel.
- tidyup report — generates a self-contained local report.html: summary counts, proposed folder tree, a thumbnail grid of detected screenshots (so I can eyeball them fast), and duplicate groups side by side.
- tidyup apply [--manifest path] [--limit N] [--only-rule name] — executes approved rows and writes logs/undo_<timestamp>.jsonl.
- tidyup undo [run-id] — reverses a run from its log.
- tidyup migrate --to D:\ [--dry-run] — moves organized folders to another drive using copy → verify → remove, resumable if interrupted. Checks free space first and refuses if there isn't enough.
- tidyup upkeep [--yes] — on-demand quick tidy, run by the user whenever they want (no scheduled task). Upkeep auto-applies ONLY high-confidence move/rename rules on Downloads, Desktop and Pictures\Screenshots, after one confirmation prompt (skipped with --yes). No review_delete actions happen automatically.
- Every command supports --config path and --dry-run. Show human-friendly output, not stack traces.

CONFIG (config.yaml, heavily commented)
- Source folders (default: the standard user folders; auto-detect whether OneDrive Known Folder Backup has redirected Documents/Desktop/Pictures into OneDrive, and don't double-count them).
- Destination root (default: organize in place; can be set to e.g. D:\Organized).
- Exclusions and the atomic-folder list.
- Category rules and keywords.
- Naming pattern.
- Delete mode: review folder or Recycle Bin.

RULES
- Target structure (defaults, editable in config): Documents/{Finance, Receipts, School, Work, Personal, Medical, Manuals, Housing, Taxes, Other}, Pictures/Photos/YYYY/YYYY-MM, Pictures/Screenshots_Review, Videos/YYYY, Installers, Archives, _Unsorted, _Duplicates, _Review_Delete.
- Screenshots: score the evidence and record the reasons in notes. Signals: filename starts with "Screenshot" / "Screen Shot" / "Capture" / "Snip"; located in Pictures\Screenshots; PNG; no EXIF camera make/model; dimensions exactly match common screen resolutions or phone screen aspect ratios. Destination: Pictures/Screenshots_Review.
- Photos: use EXIF DateTimeOriginal, fall back to the date in the filename (IMG_20240714_..., PXL_...), then to the modified date. Destination: Photos/YYYY/YYYY-MM.
- Near-duplicate photos: use perceptual hash (imagehash). Group them in the report; don't move them automatically.
- Exact duplicates: group by size, then SHA-256. Keep one copy, preferring the best location and name and the oldest created date. The others go to _Duplicates with a note pointing to the kept copy.
- Version chains: names like "report v3", "final_FINAL", "(1)", "copy of". Group these in the report as "likely old versions", approved=N.
- PDFs: extract text from the first 2 pages with pypdf, match configurable keyword lists to pick a category, and pull a date from the text if possible (else the modified date). Rename to YYYY-MM-DD_Category_Short-Description.pdf only at high confidence; otherwise keep the original name and file it under _Unsorted/PDFs. No text found (scanned PDF) → medium confidence, by filename and keywords only.
- Office docs, txt, etc.: same keyword approach (python-docx for .docx; filename-only for the rest).
- Installers (.exe, .msi) and archives (.zip, .7z, .rar) in Downloads → Installers/ and Archives/. Installers older than 90 days get flagged review_delete with approved=N.
- Anything that doesn't match a rule → _Unsorted, never left in limbo.

ENGINEERING
- Python 3.11+. Minimal dependencies: pyyaml, pypdf, Pillow, imagehash, python-docx, send2trash, tqdm. Put dev-only deps (pytest, reportlab for making test PDFs) in requirements-dev.txt.
- Package layout: tidyup/ with separate modules for scan, rules, plan, apply/undo, migrate, report, upkeep and windows helpers. The entry point is python -m tidyup.
- Tests (pytest): generate a realistic fake file tree in a temp dir with screenshots (PNG at screen resolutions), photos with EXIF camera data, PDFs with invoice/statement/syllabus text, exact duplicates, version chains, a git project folder, simulated cloud-only files (mock the attributes), and a name collision. Test that plan → apply → undo restores the tree byte-for-byte, that atomic folders are never split, that cloud-only files are never opened, and that cross-drive migrate verifies before removing.
- Add a GitHub Actions workflow that runs the tests on windows-latest and ubuntu-latest.

DOCS
- README.md written for a non-programmer: installing Python on Windows (check "Add to PATH"), downloading the repo, creating a venv, installing requirements, and a "first run" walkthrough. The walkthrough should go: scan → plan → review manifest in Excel and report.html → apply on Downloads only with --limit 50 → check → undo test → full run. Also explain the D: migration and the on-demand upkeep command.
- CLAUDE.md: the folder structure, naming conventions, rule summary and safety rules, so future Claude sessions follow the same system.
- A "Safety" section in the README listing exactly what the tool will never do.
