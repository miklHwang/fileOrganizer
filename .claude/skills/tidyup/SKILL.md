---
name: tidyup
description: Run tidyup on this Windows PC to safely organize the user's Documents, Desktop, Downloads, Pictures, Videos and OneDrive (scan, plan, review, apply, undo, migrate to D:, upkeep). Use when the user asks to tidy, organize, sort or clean up their files, move them to another drive, or undo a tidyup run.
---

# Running tidyup for the user

You are on the user's own Windows PC, in the tidyup folder. Run tidyup's commands for them and explain the results in plain English. The user is not a programmer. The full guide is `README.md`. Follow it, especially section 4 (first run).

## Ground rules (never break these)

- Change files only through tidyup's own commands. Never move, rename, copy or delete the user's files yourself (no `del`, `rm`, `Remove-Item`, `move`, `mv`, `robocopy` or ad-hoc scripts), and don't edit tidyup's code.
- **Get a yes first.** Commands that move files are `apply`, `undo` (except with `--list` or `--dry-run`), `migrate` and `upkeep`. Each one needs the user's explicit OK in this conversation for that exact command, every time. Show its dry run first and say what it will do: how many files, from where, to where. An OK for one command doesn't cover the next one.
- `apply`, `migrate` and `upkeep` ask "Apply N changes? [y/N]". They answer no by themselves when nobody can type, and you can't type into them. So once the user has said yes, add `--yes`. Never add `--yes` before they have said yes.
- Never change `approved` values in a manifest on your own. Only do it when the user asks (see "Changing approvals" below).
- Never edit `config.yaml` without showing the user the exact change and getting a yes. Never add Windows, Program Files, ProgramData or AppData as sources, and never weaken the `exclusions` or `atomic` lists.
- If a command fails, show its message, explain it, and stop. Don't work around it. Add `--debug` only to diagnose.

## How to run a command

From the tidyup folder, use the virtual environment's Python:

- Git Bash: `.venv/Scripts/python.exe -m tidyup <command> [options]`
- PowerShell: `.\.venv\Scripts\python.exe -m tidyup <command> [options]`

For a preview, put `--dry-run` straight after `tidyup`, for example `.venv/Scripts/python.exe -m tidyup --dry-run apply --limit 50`. A dry run changes nothing.

### First-time setup (only if `.venv` doesn't exist)

1. Run `python --version`. It must be 3.11 or newer. If it isn't, ask the user to install Python from python.org with "Add python.exe to PATH" ticked (README 2.1), then stop.
2. `python -m venv .venv`
3. `.venv/Scripts/python.exe -m pip install -r requirements.txt`
4. `.venv/Scripts/python.exe -m tidyup --version` should print a version.

## First cleanup: one step at a time

Report after each step and wait for the user before the next one.

1. **Check the settings.** Read `config.yaml`. Tell the user which folders will be looked at, whether files are organized in place or under a destination root, and the delete mode (`review` folder or `recycle_bin`). Ask if that's right.
2. **`scan`** is read-only. Summarize the file counts and sizes it prints.
3. **`plan`** writes `tidyup_data\manifest.csv`. It overwrites any earlier manifest and loses the user's Y/N edits in it. If a manifest exists, ask first, and if they want it kept, copy it to `tidyup_data\manifest_backup_<date>.csv` (copying tidyup's own work file is fine). Then summarize from the CSV: rows per rule and action, how many are approved Y, every OneDrive warning, and what would go to `_Review_Delete` or `_Duplicates`.
4. **`report --open`** opens `report.html` in their browser. Point them to the screenshot thumbnails and the duplicate groups. Mention they can also review `manifest.csv` in Excel and must close it before applying. Offer to answer questions about particular files.
5. **Small trial on Downloads.** Run `--dry-run apply --only-folder downloads --limit 50` and show what it would do. After a yes, run `apply --only-folder downloads --limit 50 --yes` and note the run id it prints.
6. **Check.** Ask the user to look in Downloads in File Explorer.
7. **Undo test.** Run `undo --dry-run`, then after a yes, `undo`. It has no prompt and undoes the latest run, or `undo <run id>`. Ask them to confirm the files are back.
8. **Full run.** Run `--dry-run apply`, then after a yes, `apply --yes`. If files changed since the plan, run `scan` and `plan` again first (see step 3). They can also split the work with `--only-folder NAME`, `--only-rule NAME` or `--limit N`.

## Other jobs

- **What was done / undo:** `undo --list` shows past runs. `undo <run id>` reverses one (after a yes).
- **Quick tidy when Downloads or the Desktop get messy:** `--dry-run upkeep`, then after a yes, `upkeep --yes`. It only does high-confidence moves and renames, and never review-delete or duplicates. There is no scheduled task. Never offer to create one.
- **Move to the D: drive (README section 5):** `--dry-run migrate --to D:\` writes `tidyup_data\migrate_manifest.csv` and checks free space. Have the user review that file. After a yes, run `migrate --to D:\ --yes`. If it gets interrupted, run the same command again to resume. Afterwards, walk them through changing each folder's Location in File Explorer (README section 5, step 6). tidyup doesn't do that part.

## Changing approvals

The user can change approvals in Excel themselves (save as CSV, same name). If they ask you to do it instead, for example "approve all screenshot rows", change only the rows they named. Use tidyup's own CSV functions so the file stays safe to open in Excel:

```
.venv/Scripts/python.exe - <<'EOF'
from tidyup import config, models
cfg = config.load_config(config.find_default_config())
rows = models.read_manifest(cfg.manifest_path)
changed = []
for r in rows:
    if r.rule == "screenshot" and not r.approved:   # <- the user's condition
        r.approved = True
        changed.append(r.id)
models.write_manifest(cfg.manifest_path, rows, cfg.csv.get("delimiter", ","))
print("changed rows:", changed)
EOF
```

Then tell the user which row ids changed and show the dry run before applying anything.

## Words to use

Say "move to the review folder", never "delete". tidyup never deletes: unwanted files go to `_Review_Delete`, or to the Recycle Bin if the config says so. Every applied run can be undone.
