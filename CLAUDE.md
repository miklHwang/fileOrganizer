# CLAUDE.md - tidyup

tidyup is a Python 3.11+ command-line file organizer for a Windows 10/11 PC. It is developed and tested on Linux, so every Windows-specific call sits behind small mockable functions in `winutil`. Run it with `python -m tidyup <command>`. Specs: `docs/SPEC.md` (with its amendment) and `docs/PLAN.md`. To run tidyup on the user's PC for them (not to develop it), follow the `/tidyup` skill in `.claude/skills/tidyup/SKILL.md`.

Commands: `scan`, `plan`, `report`, `apply`, `undo`, `migrate`, `upkeep`. All accept `--config` and `--dry-run`. There is NO scheduled task and no `schedule` command. Upkeep runs only when the user asks for it.

## Package layout (`tidyup/`)

Each CLI command module exposes `command(cfg, args) -> int`. `cli.py` dispatches to it via `importlib`.

| Module | Responsibility |
|---|---|
| `cli.py`, `__main__.py` | argparse, config resolution, dispatch, friendly errors (no tracebacks) |
| `config.py`, `default_config.yaml` | defaults <- config file <- overrides; resolves known folders, OneDrive de-duplication, work dir |
| `errors.py` | `TidyupError` hierarchy (`ConfigError`, `UnsafePathError`, `VerifyError`, ...) |
| `console.py` | the only module allowed to use `print()` (info, success, warn, progress) |
| `models.py` | enums (`Action`, `Confidence`), `FileRecord`, and the ONLY reader/writer of inventory/manifest CSV |
| `winutil.py` | all OS-specific code: long paths, file attributes, cloud-only detection, known folders, free space, Recycle Bin, lock errors. The only module using `subprocess`, `winreg` or `send2trash` |
| `safety.py` | protected paths, hidden/system, exclusions, atomic folders |
| `fsops.py` | the ONLY module that mutates user files (move, copy-verify, trash, mkdir) |
| `journal.py` | the ONLY reader/writer of undo logs (JSONL, fsync'd per line) |
| `extract.py` | content readers (EXIF/image info, PDF text, docx, text). Refuse cloud-only files |
| `rules.py` | per-file classification into `Proposal`s (`classify`), filename/text date parsing, PDF naming, screenshot scoring |
| `dupes.py` | exact duplicate groups and keeper choice, version chains, near-duplicate (perceptual hash) groups |
| `scan.py` | read-only inventory -> `inventory.csv` |
| `plan.py` | inventory -> `manifest.csv` (applies rules, OneDrive warnings) |
| `report.py` | self-contained `report.html` (no JS, no external URLs) |
| `apply.py`, `undo.py` | execute approved manifest rows with journaling; reverse a run |
| `migrate.py` | move organized folders to another drive (copy -> verify -> remove), resumable |
| `upkeep.py` | on-demand quick tidy of Downloads, Desktop and Screenshots |

Work files (`inventory.csv`, `manifest.csv`, `report.html`, `logs/undo_<timestamp>.jsonl`) live in `tidyup_data\` next to `config.yaml`. They are always excluded from scans.

## Target folder structure (defaults, editable in config)

- `Documents/{Finance, Receipts, School, Work, Personal, Medical, Manuals, Housing, Taxes, Other}`
- `Pictures/Photos/YYYY/YYYY-MM`, `Pictures/Screenshots_Review`, `Videos/YYYY`
- `Installers` (inside Downloads; `Archives` only with `rules.archives.keep_in_place: false`); `_Unsorted` (with subfolders like `PDFs`, `Documents`), `_Duplicates`, `_Review_Delete` (in the user profile folder)
- In place by default. Files already in OneDrive stay in OneDrive (mirrored structure). A destination root (e.g. `D:\Organized`) is optional.

## Naming conventions

- Renamed PDFs: `YYYY-MM-DD_Category_Short-Description.pdf`. Pattern and length are set under `naming:` in config. Rename only at high confidence.
- Name collisions: append ` (2)`, ` (3)`, ... (`fsops.unique_path`). Never overwrite.
- Rule names (the manifest `rule` column and `--only-rule`): `atomic_folder`, `exact_duplicate`, `installer`, `installer_old`, `archive`, `screenshot`, `photo`, `video`, `pdf`, `document`, `unsorted`, `already_in_place`, `already_filed`, `cloud_only_leave_onedrive`, `scan_error`, `migrate`. The list is `rules.RULE_NAMES`.
- Manifest actions: `move`, `rename`, `move_rename`, `review_delete`, `duplicate`, `skip`. Columns: id, action, source, destination, rule, confidence, notes, approved (Y/N).
- Module style: `from __future__ import annotations` first. Paths in memory are absolute `pathlib.Path`. Dates are naive local datetimes: `YYYY-MM-DD HH:MM:SS` in CSV, ISO `T` form in JSONL.

## Rules and confidence

High confidence defaults to approved Y. Medium and low default to N.

| Rule | Behaviour | Confidence |
|---|---|---|
| screenshot | score of filename prefix, Screenshots folder, PNG, no camera EXIF, screen/phone dimensions; reasons go in notes. Goes to `Pictures/Screenshots_Review` | high at score >= 5, medium at 3-4 |
| photo | date from EXIF DateTimeOriginal, then filename (IMG_/PXL_...), then modified date. Goes to `Photos/YYYY/YYYY-MM` | high for EXIF or filename date, medium for modified date |
| video | `Videos/YYYY` | high for filename date, else medium |
| pdf | first 2 pages of text, keyword category, date from text. Renamed only at high confidence, else `_Unsorted/PDFs` with the original name | high with enough keyword hits; scanned/no text is medium (filename/keywords only); no match is low |
| document | docx via python-docx, other formats by filename only. Moved, never renamed | docx strong text match high, filename match medium, none low (`_Unsorted/Documents`) |
| installer | in Downloads goes to `Installers/` | high |
| archive | left in its folder (skip); `keep_in_place: false` → Downloads/Archives | high |
| installer_old | installers older than 90 days become `review_delete` | low, approved N |
| exact_duplicate | group by size, then SHA-256; keeper by best location, name and oldest created date; others go to `_Duplicates` with a note naming the kept copy | high (approved Y) |
| atomic_folder | whole folder, left in place (or moved whole with a destination root) | medium, approved N when moved |
| unsorted | anything unmatched goes to `_Unsorted/<type>`; never left in limbo | low |

Version chains ("v3", "final_FINAL", "(1)", "copy of") and near-duplicate photos (imagehash) are only grouped in the report, never moved automatically.

## Hard safety rules and where each is enforced

1. Never hard-delete. "Delete" means a move to `_Review_Delete`, or the Recycle Bin via send2trash if config says so. Enforced in `fsops` (`trash`, `move_*`) and `winutil.send_to_recycle_bin`. `tests/test_static_safety.py` scans the source with `ast` for forbidden calls (remove, unlink, rmtree, replace, shutil.move, ...) outside an allowlist.
2. Never touch Windows, Program Files*, ProgramData, AppData, $Recycle.Bin, System Volume Information, or hidden/system files. Enforced in `safety.is_protected`, `is_hidden_or_system`, `assert_not_protected`, `check_source`. Dot-files count as hidden on all OSes.
3. Atomic folders (markers .git, package.json, *.sln, *.csproj, pyproject.toml, node_modules, venv/.venv/pyvenv.cfg, plus app folder names) move whole and are never reorganized inside. Enforced in `safety.is_atomic_dir` and `find_atomic_ancestor`, via `check_source`. The lists are in config (`atomic`). Source roots are never atomic.
4. Every change goes through a manifest, and apply runs only rows with approved=Y. Enforced in `apply.py`. Upkeep never auto-applies `review_delete`, `duplicate` or `skip` (`upkeep._NEVER`). It accepts only high-confidence move/rename rows.
5. Every applied action is journaled for undo, and `undo` fully reverses a run. Enforced in `journal.py`, `fsops` (journal written only after success) and `undo.py`.
6. Never overwrite: no-clobber rename plus `unique_path`, in `fsops`. `fsops` refuses same-file moves (data-loss fix).
7. Preserve timestamps (copy2 semantics) in `fsops`.
8. Cross-drive move is copy -> SHA-256 verify -> only then remove the source (`fsops._copy_verified`, `move_tree`). `~tidyup-partial-*` temp files are excluded from scans.
9. Long paths: every OS call gets `winutil.long_path(p)`. Stored paths never carry `\\?\` (`strip_long_path`).
10. Locked or in-use files are skipped, logged, and the run continues (`winutil.is_lock_error`, handled in `fsops` and `apply`).
11. Cloud-only (OneDrive placeholder) files are never read. No hashing, PDF text or EXIF. Enforced by the `extract` readers (they return nothing for cloud-only files) and `winutil.assert_local` in `fsops.sha256_file`. `rules.py` classifies them by metadata only. Moves out of OneDrive get a warning note (`plan.ONEDRIVE_WARNING`).
12. `migrate` refuses a target that overlaps the source folders (`migrate._overlaps`), checks free space first, and needs a reviewed manifest.
13. CSV cells are guarded against formula injection (`models`, leading apostrophe for `= + - @` cells). Do not bypass this by writing CSVs elsewhere.
14. `safety.TRUSTED_PREFIXES` is a TEST-ONLY hook. Production value is `()`. It must NEVER be settable from config, env vars or CLI. Only `tests/conftest.py` patches it.

### Never add a delete path outside fsops

Only `fsops.py` may mutate user files. Do not call `os.remove`, `os.unlink`, `os.rmdir`, `shutil.rmtree`, `os.replace`, `shutil.move`, `shutil.copy*` or `send2trash` anywhere else. Go through `fsops` (or `winutil.send_to_recycle_bin` for the Recycle Bin option). Likewise: only `models.py` touches inventory/manifest CSVs, only `journal.py` touches undo logs, and only `winutil.py` calls `subprocess`, `winreg` or reads `st_file_attributes`. `test_static_safety.py` will fail if you break these.

## Mockability conventions

- Import helpers as modules and call through the attribute: `from tidyup import winutil` then `winutil.get_file_attributes(p)`. Never `from tidyup.winutil import get_file_attributes`. The same applies to `fsops`, `extract`, `safety`, `plan`, `scan`, `apply`. Tests use `monkeypatch.setattr(winutil, "...", fake)`.
- Windows-only behaviour (attributes, known folders, OneDrive root, volumes, free space, Recycle Bin) belongs in `winutil` as small functions.
- User-facing output goes through `console`, and errors are `TidyupError` subclasses with plain-English messages.

## Tests

- Run: `python -m pytest -q` (dev deps: `pip install -r requirements-dev.txt`). CI runs ubuntu-latest and windows-latest on Python 3.11 and 3.12 (`.github/workflows/tests.yml`).
- `tests/fixtures/fake_tree.py` builds a realistic fake tree (screenshots, EXIF photos, PDFs, duplicates, version chains, a git project, cloud-only and hidden files, a name collision). Import it as `from fixtures.fake_tree import ...`, not `tests.fixtures`.
- `tests/conftest.py` provides these:
  - `fake_tree`
  - `cfg`
  - `mock_attrs`: fakes cloud-only and hidden attributes.
  - `cross_volume`: forces `same_volume` to False.
  - `open_spy`: records every opened path, lowercased on Windows via `norm_key`. Use it to prove cloud-only files are never opened.
  - an autouse fixture that sets `safety.TRUSTED_PREFIXES` to the pytest temp base, because temp dirs sit under AppData on Windows.
- Required invariants: plan -> apply -> undo restores the tree byte-for-byte; atomic folders are never split; cloud-only files are never opened; migrate verifies before removing.
- `tests/test_e2e.py` holds the end-to-end flow. Add or update tests with every behaviour change.
