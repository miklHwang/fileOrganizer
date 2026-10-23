# tidyup — Implementation Plan

Source of truth for requirements: `docs/SPEC.md`. This file is the source of truth for **interfaces, file ownership and task order**. Builders code against the interfaces in §2 without seeing each other's work. If an interface here is wrong or missing, stop and report; do not invent a different one.

## Progress

Phase 1 (foundation): [x] P1-T1 [x] P1-T2 [x] P1-T3 [x] P1-T4 [x] P1-T5 [x] P1-T6
Phase 2 (building blocks): [x] P2-T1 [x] P2-T2 [x] P2-T3 [x] P2-T4 ~~P2-T5~~ (removed) [x] P2-T6
Phase 3 (commands): [ ] P3-T1 [ ] P3-T2 [ ] P3-T3 [ ] P3-T4 [ ] P3-T5 [ ] P3-T6
Phase 4 (planning + upkeep): [ ] P4-T1 [ ] P4-T2
Phase 5 (integration + docs): [ ] P5-T1 [ ] P5-T2 [ ] P5-T3

22 tasks (P2-T5 schedule removed: tidyup is run as needed, no scheduled task). Per-task checkboxes are repeated in §4.

---

## 0. Global conventions (every task must follow)

1. Python 3.11+, `from __future__ import annotations` in every module. Standard library + `requirements.txt` only.
2. **Mockability rule:** modules import helpers as modules and call them through the module attribute: `from tidyup import winutil` then `winutil.get_file_attributes(p)`. Never `from tidyup.winutil import get_file_attributes`. Same for `fsops`, `extract`, `safety`, `plan`, `scan`, `apply` when used across modules. Tests rely on `monkeypatch.setattr(winutil, "...", fake)`.
3. **All real OS calls** (`os.scandir`, `os.stat`, `os.lstat`, `open`, `os.rename`, `os.mkdir`, `os.rmdir`, `os.unlink`, `shutil.copy2`, `shutil.copystat`, `os.utime`) receive `winutil.long_path(p)`. Paths stored in records/CSV/logs never carry the `\\?\` prefix (`winutil.strip_long_path`).
4. Paths in memory are `pathlib.Path`, absolute. Dates are naive local `datetime`. CSV/JSON date format: `YYYY-MM-DD HH:MM:SS` (CSV) and ISO `YYYY-MM-DDTHH:MM:SS` (JSONL).
5. Only `tidyup/models.py` reads/writes inventory/manifest CSV. Only `tidyup/journal.py` reads/writes undo logs. Only `tidyup/fsops.py` mutates user files. Only `tidyup/winutil.py` calls `subprocess`, `winreg`, `send2trash`, reads `st_file_attributes`.
6. Forbidden everywhere except the allowlist in §2 (enforced by `tests/test_static_safety.py`): `os.remove`, `os.unlink`, `os.rmdir`, `os.removedirs`, `os.replace`, `shutil.rmtree`, `shutil.move`, any `.unlink(` / `.rmdir(` call, `send2trash`.
7. User-facing failures raise a subclass of `errors.TidyupError` with a plain-English message. Print through `tidyup.console`, never bare `print` in library code (except `console.py`).
8. Tests must pass on ubuntu-latest and windows-latest: use `tmp_path`, close every file/PIL image (`with`), never depend on POSIX permissions, never call real Windows APIs (mock `winutil`). Windows-only behaviour is tested by mocking `winutil.is_windows`, `winutil.get_file_attributes`, `winutil.same_volume`, `winutil.free_space`.
9. Module stubs created in P1-T1 contain the exact signatures and dataclasses from §2 with bodies `raise NotImplementedError`. The task that fills a module keeps every listed signature/dataclass unchanged; it may add private helpers (`_name`).
10. Test imports: there is no `tests/__init__.py`; import fixtures as `from fixtures.fake_tree import ...` (not `tests.fixtures`). Fake-tree handles available beyond §3: `version_1..3`, `hidden_dot`, `hidden_attr`, `protected_appdata`, `protected_recycle`, `protected_programfiles`, `excluded_desktop_ini`, `excluded_crdownload`, `git_project`, `node_project`, `app_folder` (dirs), `long_path_file`.

---

## 1. Architecture

### 1.1 Repository layout

```
requirements.txt            pyyaml>=6.0, pypdf>=4.0, Pillow>=10.0, imagehash>=4.3, python-docx>=1.1, send2trash>=1.8, tqdm>=4.66
requirements-dev.txt        -r requirements.txt, pytest>=8.0, reportlab>=4.0
pyproject.toml              [tool.pytest.ini_options] testpaths=["tests"], addopts="-ra"  (no build backend needed)
.gitignore                  __pycache__/, *.pyc, .venv/, venv/, tidyup_data/, .pytest_cache/
config.yaml                 heavily commented user config (byte-identical copy of tidyup/default_config.yaml)
README.md, CLAUDE.md
.github/workflows/tests.yml
tidyup/
  __init__.py      __version__ = "0.1.0"
  __main__.py      from tidyup.cli import main; raise SystemExit(main())
  errors.py        exception hierarchy
  console.py       human-friendly output, progress bars, sizes, confirm
  winutil.py       ALL Windows-specific / OS-specific helpers (mockable)
  models.py        dataclasses, enums, type groups, inventory + manifest CSV I/O
  journal.py       undo-log JSONL writer/reader, run listing
  config.py        config loading, defaults, validation, path resolution
  default_config.yaml  packaged defaults (source of truth for defaults)
  safety.py        protected/hidden/excluded/atomic checks
  fsops.py         the ONLY module that mutates files: move/copy-verify-remove/trash/mkdir
  extract.py       content readers (EXIF, image size, phash, PDF/DOCX text, thumbnails); cloud-only guarded
  dupes.py         exact duplicates, keeper choice, version chains, near-duplicates (pure)
  rules.py         per-file classification -> Proposal (pure apart from extract calls)
  scan.py          `tidyup scan`
  plan.py          `tidyup plan`
  report.py        `tidyup report`
  apply.py         `tidyup apply`
  undo.py          `tidyup undo`
  migrate.py       `tidyup migrate`
  upkeep.py        `tidyup upkeep` (on-demand quick tidy; no scheduled task)
  cli.py           argparse, config resolution, dispatch, friendly errors
tests/
  conftest.py              shared fixtures (fake tree, cfg, mocks, open spy)
  fixtures/__init__.py
  fixtures/fake_tree.py    fake-file-tree generator + snapshot()
  test_<module>.py         one per module (owned by that module's task)
  test_static_safety.py    AST scan for forbidden calls
  test_e2e.py              end-to-end safety properties
```

### 1.2 Data flow

`scan` (walk + stat; hash only size-collisions; image info) → `inventory.csv` → `plan` (dupes + rules + destination resolution + collision reservation) → `manifest.csv` → user reviews in Excel + `report.html` → `apply` (approved rows only, via fsops, journaled) → `logs/undo_<run_id>.jsonl` → `undo` (reverse the journal). `migrate` builds `migrate_manifest.csv` (dry run) then executes it via fsops with copy→verify→remove, journaled and resumable. `upkeep` = scan(upkeep sources) → plan → auto-apply high-confidence move/rename rows.

Work files live in `cfg.work_dir` (default `tidyup_data/` next to config.yaml): `inventory.csv`, `manifest.csv`, `report.html`, `migrate_manifest.csv`, `logs/`.

### 1.3 Module interfaces

#### errors.py (P1-T1)
```python
class TidyupError(Exception): ...          # message shown to user, no traceback
class ConfigError(TidyupError): ...
class UnsafePathError(TidyupError): ...    # protected / hidden / excluded / inside atomic folder
class CloudOnlyError(TidyupError): ...     # attempted content read or cross-volume copy of a placeholder
class FileLockedError(TidyupError): ...    # in use / permission denied
class VerifyError(TidyupError): ...        # SHA-256 mismatch after copy
class InsufficientSpaceError(TidyupError): ...
class ManifestError(TidyupError): ...
class NotSupportedError(TidyupError): ...  # e.g. a Windows-only operation on non-Windows
```

#### console.py (P1-T1)
```python
def info(msg: str) -> None            # stdout
def success(msg: str) -> None         # stdout, prefix "OK: "
def warn(msg: str) -> None            # stdout, prefix "WARNING: "
def error(msg: str) -> None           # stderr, prefix "Error: "
def heading(msg: str) -> None         # blank line + msg + underline of "-"
def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None   # aligned plain-text columns
def human_size(n: int) -> str         # 1024-based, one decimal: "0 B", "1.5 KB", "3.2 GB"
def progress(iterable: Iterable[T], *, desc: str, total: int | None = None, unit: str = "file") -> Iterator[T]
    # tqdm(..., disable=None) so it is silent when stderr is not a TTY
def confirm(prompt: str, *, assume_yes: bool = False) -> bool
    # assume_yes -> True; stdin not a TTY -> False; else input() accepting y/yes (case-insensitive)
```

#### winutil.py (P1-T2)
```python
FILE_ATTRIBUTE_READONLY = 0x1; FILE_ATTRIBUTE_HIDDEN = 0x2; FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10; FILE_ATTRIBUTE_REPARSE_POINT = 0x400; FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000; FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ONLY_MASK = 0x400000 | 0x40000 | 0x1000
LONG_PATH_THRESHOLD = 240

def is_windows() -> bool                                  # os.name == "nt"
def long_path(path: str | os.PathLike) -> str
    # not is_windows(): return os.fspath(path) unchanged.
    # Windows: s = os.fspath(path); already starts with "\\\\?\\" -> s; len(s) < 240 -> s;
    # absolute UNC "\\\\server\\share\\x" -> "\\\\?\\UNC\\server\\share\\x"; absolute drive path ->
    # "\\\\?\\" + ntpath.normpath(s); relative -> make absolute with os.path.abspath first.
    # Pure string logic (uses ntpath) so it is testable on Linux with is_windows mocked True.
def strip_long_path(path: str | os.PathLike) -> str       # inverse; "\\\\?\\UNC\\" -> "\\\\"
def get_file_attributes(path: str | os.PathLike, st: os.stat_result | None = None) -> int
    # Windows: (st or os.lstat(long_path(path))).st_file_attributes; non-Windows: 0. Raises OSError if missing.
def is_cloud_only_attrs(attrs: int) -> bool               # bool(attrs & CLOUD_ONLY_MASK)
def is_cloud_only(path) -> bool                           # is_cloud_only_attrs(get_file_attributes(path)); False on OSError
def assert_local(path, known_cloud_only: bool = False) -> None
    # raise CloudOnlyError(f"... is an online-only OneDrive file; not reading it") if known_cloud_only or is_cloud_only(path)
def is_hidden_or_system(name: str, attrs: int) -> bool
    # attrs & (HIDDEN|SYSTEM) -> True; on non-Windows also name.startswith(".")
def file_times(st: os.stat_result) -> tuple[datetime, datetime]
    # (created, modified). created = st_birthtime if present, elif is_windows(): st_ctime, else min(st_ctime, st_mtime)
def norm_key(path) -> str                                 # os.path.normcase(os.path.normpath(os.path.abspath(strip_long_path(path))))
def is_within(child, parent) -> bool                      # child == parent or child is inside parent, using norm_key component-wise
def known_folders() -> dict[str, Path | None]
    # keys exactly: home, documents, desktop, downloads, pictures, videos, onedrive
    # Windows: winreg HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders values
    #   "Personal"->documents, "Desktop", "My Pictures"->pictures, "My Video"->videos,
    #   "{374DE290-123F-4565-9164-39C4925E467B}"->downloads; expand %VARS%; fallback home/<Name>.
    # Non-Windows: home/Documents etc. Missing folders -> None. onedrive = onedrive_root().
def onedrive_root() -> Path | None
    # first existing of env OneDrive, OneDriveConsumer, OneDriveCommercial; else home/"OneDrive" if it exists; else None
def same_volume(a, b) -> bool          # st_dev of nearest existing ancestor of each path are equal
def free_space(path) -> int            # shutil.disk_usage(nearest existing ancestor).free
def is_lock_error(exc: OSError) -> bool
    # True for PermissionError, getattr(exc,"winerror",None) in (5, 32, 33), errno in (EACCES, EBUSY, ETXTBSY)
def send_to_recycle_bin(path) -> None  # send2trash.send2trash(strip_long_path(path))
```

#### models.py (P1-T3)
```python
class Action(StrEnum): MOVE="move"; RENAME="rename"; MOVE_RENAME="move_rename"; REVIEW_DELETE="review_delete"; DUPLICATE="duplicate"; SKIP="skip"
class Confidence(StrEnum): HIGH="high"; MEDIUM="medium"; LOW="low"
class EntryType(StrEnum): FILE="file"; ATOMIC_FOLDER="atomic_folder"

TYPE_GROUPS: dict[str, frozenset[str]]   # group -> lowercase extensions with dot
#  Images: .jpg .jpeg .png .gif .bmp .webp .heic .heif .tif .tiff .svg .ico .dng .cr2 .nef .arw .raw
#  Videos: .mp4 .mov .avi .mkv .wmv .m4v .3gp .webm .mts .m2ts .flv
#  Audio: .mp3 .wav .flac .m4a .aac .ogg .wma
#  PDFs: .pdf
#  Documents: .doc .docx .xls .xlsx .ppt .pptx .txt .rtf .odt .ods .odp .md .csv .pages .numbers .key .epub
#  Installers: .exe .msi .msix .appx
#  Archives: .zip .7z .rar .tar .gz .tgz .bz2 .xz .iso
def type_group(extension: str) -> str     # one of the keys above, or "Other"; case-insensitive

@dataclass
class FileRecord:
    path: Path
    entry_type: EntryType
    source_root: str              # label of the SourceRoot that was scanned
    size: int                     # bytes; atomic folder = sum of contained file sizes
    extension: str                # lowercase incl. dot, "" if none; "" for atomic folders
    created: datetime
    modified: datetime
    is_cloud_only: bool
    sha256: str | None = None     # only local files whose size collides with another file
    image_width: int | None = None
    image_height: int | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    exif_datetime: datetime | None = None
    phash: str | None = None      # 16-hex-char imagehash.phash string
    atomic_root: Path | None = None   # for atomic folders: their own path
    file_count: int = 1           # atomic folders: number of files inside
    error: str = ""               # e.g. "locked: <msg>", "unreadable image"

INVENTORY_COLUMNS = ["path","entry_type","source_root","size","extension","created","modified",
  "is_cloud_only","sha256","image_width","image_height","camera_make","camera_model",
  "exif_datetime","phash","atomic_root","file_count","error"]

@dataclass
class ManifestRow:
    id: int
    action: Action
    source: Path
    destination: Path | None      # None for skip and for review_delete in recycle_bin mode
    rule: str
    confidence: Confidence
    notes: str                    # "; "-joined
    approved: bool

MANIFEST_COLUMNS = ["id","action","source","destination","rule","confidence","notes","approved"]

def format_dt(dt: datetime | None) -> str          # "YYYY-MM-DD HH:MM:SS" or ""
def parse_dt(s: str) -> datetime | None            # accepts that format, ISO with "T", and "YYYY-MM-DD HH:MM" (Excel re-save); "" -> None
def write_inventory(path: Path, records: Iterable[FileRecord], delimiter: str = ",") -> int    # returns row count
def read_inventory(path: Path) -> list[FileRecord]
def write_manifest(path: Path, rows: Iterable[ManifestRow], delimiter: str = ",") -> int
def read_manifest(path: Path) -> list[ManifestRow]
```
CSV rules: encoding `utf-8-sig` (BOM so Excel shows accents), `newline=""`, `csv.QUOTE_MINIMAL`, header row = column list exactly, booleans `Y`/`N`, `None` → empty cell, paths via `str()` without long-path prefix. Parent directory created if missing. `read_manifest` is tolerant of an Excel re-save: try `utf-8-sig`, fall back to `cp1252`; sniff delimiter among `,` `;` `\t` from the header line; header names matched case-insensitively after strip; blank rows skipped; `approved` true for `y/yes/true/1/x` (case-insensitive), else false; invalid action/confidence or missing column → `ManifestError` naming the row number.

#### journal.py (P1-T3)
Undo log file: `<logs_dir>/undo_<run_id>.jsonl`, UTF-8, one JSON object per line, `flush()` + `os.fsync()` after every line. `run_id` = local time `YYYYMMDD-HHMMSS`, with `-2`, `-3`… appended if the file already exists. Paths are plain strings (no `\\?\`).

Record types (exact keys):
```json
{"type":"run_start","run_id":"20261023-141500","kind":"apply","started":"2026-10-23T14:15:00","tool_version":"0.1.0","meta":{"config":"...","manifest":"...","to":null,"undoes":null}}
{"type":"mkdir","seq":1,"path":"C:\\Users\\me\\Documents\\Finance","time":"..."}
{"type":"move","seq":2,"row_id":"17","action":"move","src":"...","dst":"...","is_dir":false,"cross_volume":false,"size":1234,"sha256":null,"time":"..."}
{"type":"trash","seq":3,"row_id":"18","src":"...","time":"..."}
{"type":"skip","row_id":"19","src":"...","reason":"locked: file is in use","time":"..."}
{"type":"error","row_id":"20","src":"...","reason":"...","time":"..."}
{"type":"run_end","finished":"...","counts":{"moved":10,"trashed":0,"skipped":2,"errors":0}}
{"type":"undone","by":"20261023-150000","time":"..."}
```
`kind` ∈ `apply | upkeep | migrate | undo`. `seq` increments per mutating record (mkdir/move/trash). `sha256` is set for cross-volume moves (verified hash), else null. `undone` is appended to the *original* log by undo.

```python
class Journal:
    path: Path; run_id: str; kind: str
    @classmethod
    def create(cls, logs_dir: Path, kind: str, meta: dict) -> Journal        # makes logs_dir, writes run_start
    @classmethod
    def resume(cls, path: Path) -> Journal                                  # reopen unfinished log for append; seq continues
    def mkdir(self, path: Path) -> None
    def move(self, *, src: Path, dst: Path, row_id: str | None, action: str, is_dir: bool,
             cross_volume: bool, size: int, sha256: str | None) -> None
    def trash(self, *, src: Path, row_id: str | None) -> None
    def skip(self, *, src: Path, row_id: str | None, reason: str) -> None
    def error(self, *, src: Path, row_id: str | None, reason: str) -> None
    def finish(self, counts: dict[str, int]) -> None                          # writes run_end, closes
    def close(self) -> None                                                   # close without run_end (crash/interrupt)
    def __enter__/__exit__                                                    # __exit__ closes; does NOT write run_end

@dataclass
class RunLog:
    path: Path; run_id: str; kind: str; header: dict
    entries: list[dict]          # every record after run_start, file order (run_end/undone included)
    finished: bool               # run_end present
    undone_by: str | None        # from last "undone" record
    @property
    def ops(self) -> list[dict]  # entries with type in {"mkdir","move","trash"}, file order

def new_run_id(logs_dir: Path) -> str
def read_run(path: Path) -> RunLog          # ignores a truncated/invalid final line
def list_runs(logs_dir: Path) -> list[RunLog]   # all undo_*.jsonl sorted by run_id ascending; [] if dir missing
def find_run(logs_dir: Path, run_id: str | None) -> RunLog
    # run_id given: exact match else TidyupError. None: latest run with undone_by None and kind != "undo";
    # none -> TidyupError("No runs to undo").
def mark_undone(run: RunLog, by_run_id: str) -> None   # appends {"type":"undone",...}
```

#### config.py (P1-T4)
```python
FOLDER_KEYS = ("home","documents","desktop","downloads","pictures","videos","onedrive","screenshots")

@dataclass(frozen=True)
class SourceRoot:
    label: str          # folder key ("documents") or, for a literal path in `sources`, its folder name
    path: Path
    in_onedrive: bool

@dataclass(frozen=True)
class Config:
    path: Path | None                     # config file used; None = packaged defaults
    config_dir: Path                      # dir of config file, or cwd when path is None
    folders: dict[str, Path | None]       # all FOLDER_KEYS, resolved, None if unavailable
    sources: tuple[SourceRoot, ...]       # resolved, existing, de-duplicated (see below)
    dest_root: Path | None                # None = organize in place
    work_dir: Path
    delete_mode: str                      # "review" | "recycle_bin"
    exclusions: dict                      # {"names": [glob...], "paths": [Path...]}
    atomic: dict                          # {"markers": [glob...], "folder_names": [glob...]}
    categories: dict[str, dict]           # ordered {"Finance": {"keywords": [...]}, ...}
    structure: dict[str, str]
    naming: dict
    rules: dict
    upkeep: dict
    migrate: dict
    report: dict
    csv: dict
    raw: dict                             # fully merged dict

    @property inventory_path -> Path       # work_dir/"inventory.csv"
    @property manifest_path -> Path        # work_dir/"manifest.csv"
    @property report_path -> Path          # work_dir/"report.html"
    @property migrate_manifest_path -> Path  # work_dir/"migrate_manifest.csv"
    @property logs_dir -> Path             # work_dir/"logs"
    def with_sources(self, labels: Sequence[str]) -> Config   # dataclasses.replace with re-resolved sources
    def folder_containing(self, path: Path) -> str | None
        # key of the most specific (longest) folder in `folders` containing path (winutil.is_within); "home" only if nothing else
    def in_onedrive(self, path: Path) -> bool  # folders["onedrive"] is not None and is_within(path, onedrive)
    def resolve_destination(self, logical_dir: str, source: Path) -> Path    # see rules below

def load_config(path: Path | None = None, overrides: dict | None = None) -> Config
    # path None -> packaged defaults only. Merge order: defaults <- file <- overrides (deep_merge).
def find_default_config(cwd: Path | None = None) -> Path | None   # cwd/"config.yaml" if it exists
def default_config_text() -> str     # importlib.resources.files("tidyup").joinpath("default_config.yaml").read_text("utf-8")
def deep_merge(base: dict, override: dict) -> dict   # dicts merge recursively; lists/scalars replace
```
Resolution rules:
- `folders.*: auto` → `winutil.known_folders()[key]`; `screenshots: auto` → `pictures/"Screenshots"`. Strings expand `~` and `%VAR%`/`$VAR`. `null` → None.
- `sources`: each entry is a folder key or a literal path. Drop entries that are None or missing on disk (console.warn once). De-duplicate: if a source is inside another source (OneDrive Known Folder Backup puts Documents inside OneDrive), drop the inner one and `console.info` that it is covered by the outer one. `in_onedrive` = `cfg.in_onedrive(path)`.
- `work_dir` relative → relative to `config_dir`.
- `exclusions.paths` → list[Path].
- Validation (raise `ConfigError` with the key name): unknown top-level key; `delete_mode` not in {review, recycle_bin}; `version != 1`; YAML syntax error (include line number); structure templates whose first segment is not one of the roots below.
- `resolve_destination(logical_dir, source)`: `logical_dir` is POSIX-style, e.g. `"Documents/Finance"`, `"Pictures/Photos/2024/2024-07"`, `"_Unsorted/PDFs"`.
  - `dest_root` set → `dest_root / logical_dir`.
  - In place: first segment (case-insensitive) `Documents|Pictures|Videos` → `folders[documents|pictures|videos] / rest`; `Installers|Archives` → `folders["downloads"] / logical_dir`; `_Unsorted|_Duplicates|_Review_Delete` → `folders["home"] / logical_dir`. If the needed folder is None, fall back to `home / logical_dir`.
  - OneDrive guard (in place only): if `in_onedrive(source)` and the result is not in OneDrive → `folders["onedrive"] / logical_dir` instead (in-place organizing never removes a file from the cloud).

#### config.yaml schema (P1-T4; `tidyup/default_config.yaml` == repo `config.yaml`)
Every key below must exist in the default file with a comment explaining it in plain English.
```yaml
version: 1
folders:            # auto = detect; or a path; or null
  home: auto
  documents: auto
  desktop: auto
  downloads: auto
  pictures: auto
  videos: auto
  onedrive: auto
  screenshots: auto           # auto = <pictures>\Screenshots
sources: [documents, desktop, downloads, pictures, videos, onedrive]
destination:
  root: null                  # null = organize in place; e.g. 'D:\Organized'
work_dir: tidyup_data         # relative to this file's folder
delete_mode: review           # review = move to _Review_Delete; recycle_bin = send2trash
exclusions:
  names: ["desktop.ini", "thumbs.db", "~$*", "*.tmp", "*.crdownload", "*.part", "*.partial", "*.lnk", "*.url", "~tidyup-partial-*"]
  paths: []
atomic:
  markers: [".git", "package.json", "*.sln", "*.csproj", "pyproject.toml", "node_modules", "venv", ".venv", "pyvenv.cfg"]
  folder_names: ["My Games", "Outlook Files", "OneNote Notebooks", "Zoom", "Visual Studio*", "Adobe",
                 "Custom Office Templates", "WindowsPowerShell", "PowerShell", "IISExpress",
                 "SQL Server Management Studio*", "Sound Recordings", "My Data Sources", "Fax", "Scanned Documents"]
structure:
  categories_root: Documents                      # Documents/<Category>
  photos: "Pictures/Photos/{yyyy}/{yyyy}-{mm}"
  screenshots: Pictures/Screenshots_Review
  videos: "Videos/{yyyy}"
  installers: Installers
  archives: Archives
  unsorted: _Unsorted                             # _Unsorted/<TypeGroup>, PDFs -> _Unsorted/PDFs
  duplicates: _Duplicates
  review_delete: _Review_Delete
categories:                   # order = tie-break priority; keywords are case-insensitive whole words/phrases
  Finance:  {keywords: [bank, statement, statement period, account number, balance, interest, credit card, investment, dividend, payroll, transaction]}
  Receipts: {keywords: [receipt, invoice, order number, order confirmation, subtotal, amount paid, total paid, thank you for your purchase]}
  School:   {keywords: [syllabus, course, semester, assignment, professor, lecture, grading, transcript, university, homework]}
  Work:     {keywords: [agenda, meeting, project, action items, contract, employee, offer letter, timesheet, resume]}
  Personal: {keywords: [passport, birth certificate, wedding, letter, personal, family, travel]}
  Medical:  {keywords: [patient, diagnosis, prescription, doctor, clinic, hospital, lab results, medical, dental]}
  Manuals:  {keywords: [manual, user guide, instructions, warranty, installation guide, quick start, safety information]}
  Housing:  {keywords: [lease, rent, landlord, tenant, mortgage, property, utility bill, electricity, hoa]}
  Taxes:    {keywords: [tax return, w-2, 1099, irs, form 1040, tax year, hmrc, deduction, taxes]}
  Other:    {keywords: []}
naming:
  pdf_pattern: "{date}_{category}_{description}"  # ".pdf" appended
  date_format: "%Y-%m-%d"
  description_max_len: 40
rules:
  screenshot:
    name_prefixes: ["screenshot", "screen shot", "capture", "snip"]
    weights: {name_prefix: 3, in_screenshots_folder: 3, png: 1, no_camera_exif: 1, screen_resolution: 2, phone_aspect: 1, has_camera_exif: -3}
    min_score: 3
    high_score: 5
    resolutions: ["1280x720","1280x800","1366x768","1440x900","1536x864","1600x900","1680x1050","1920x1080",
                  "1920x1200","2256x1504","2560x1080","2560x1440","2560x1600","2736x1824","2880x1800","3024x1964",
                  "3440x1440","3840x2160","750x1334","828x1792","1080x1920","1080x2340","1080x2400","1170x2532",
                  "1179x2556","1242x2688","1284x2778","1290x2796","1440x3200"]
    phone_aspect_ratios: [1.7778, 2.0, 2.1111, 2.1667, 2.2222]   # long side / short side, portrait only
    aspect_tolerance: 0.01
  photos:
    extensions: [.jpg, .jpeg, .heic, .heif, .dng, .cr2, .nef, .arw, .tif, .tiff]
  videos:
    extensions: [.mp4, .mov, .avi, .mkv, .wmv, .m4v, .3gp, .webm, .mts, .m2ts]
  installers: {extensions: [.exe, .msi], only_in: [downloads], review_delete_after_days: 90}
  archives:   {extensions: [.zip, .7z, .rar], only_in: [downloads]}
  pdf:        {max_pages: 2, high_min_hits: 2, date_order: MDY}
  documents:
    text_extensions: [.docx]
    filename_only_extensions: [.doc, .xls, .xlsx, .ppt, .pptx, .txt, .rtf, .odt, .ods, .odp, .md, .csv, .pages, .numbers, .key, .epub]
  duplicates: {min_size: 1, location_priority: [documents, pictures, videos, onedrive, desktop, downloads]}
  near_duplicates: {enabled: true, max_distance: 6}
  version_chains:
    patterns: ['^copy of\s+', '\s*-\s*copy(\s*\(\d+\))?$', '\s*[\(\[]\d+[\)\]]$',
               '[\s_-]*v(er(sion)?)?[\s_.-]?\d+$', '([\s_-]*final)+$', '[\s_-]*(draft|old|new|rev\d*|updated)$']
upkeep:
  sources: [downloads, desktop, screenshots]
  actions: [move, rename, move_rename]
migrate:
  folders: [documents, pictures, videos, downloads, "{home}/_Unsorted", "{home}/_Duplicates", "{home}/_Review_Delete"]
  include_onedrive: false
  free_space_margin_mb: 1024
report: {max_thumbnails: 400, thumbnail_px: 160}
csv: {delimiter: ","}
```
Placeholders `{yyyy}`, `{mm}` (zero-padded) are filled by rules; `{home}`, `{documents}`… in `migrate.folders` are folder keys.

#### safety.py (P1-T6)
```python
PROTECTED_NAMES = frozenset({"programdata", "appdata", "$recycle.bin", "system volume information"})
PROTECTED_GLOBS = ("program files*",)
TRUSTED_PREFIXES: tuple[Path, ...] = ()   # production default: empty (strict). Tests only (conftest autouse
    # fixture monkeypatches it to (tmp_path_factory.getbasetemp(),)) because on windows-latest pytest temp dirs live
    # under C:\Users\<u>\AppData\Local\Temp. Never set it from config, env vars or CLI.
def is_protected(path: Path) -> bool
    # any path component (casefolded) in PROTECTED_NAMES or matching PROTECTED_GLOBS; or the first component
    # after the drive anchor is "windows" (C:\Windows); or is_within(path, %SystemRoot%) when that env var is set.
    # If path is within a TRUSTED_PREFIXES entry, the PROTECTED_NAMES/GLOBS check applies only to the components
    # BELOW that prefix (so <basetemp>/.../home/AppData/x is still protected); the Windows/SystemRoot checks always apply.
def is_hidden_or_system(path: Path, attrs: int | None = None) -> bool
    # attrs None -> winutil.get_file_attributes(path) (0 on OSError); return winutil.is_hidden_or_system(path.name, attrs)
def is_excluded(path: Path, cfg: Config) -> bool
    # is_protected OR name matches exclusions.names (fnmatch, casefold) OR within any exclusions.paths
    # OR within cfg.work_dir OR name starts with fsops.PARTIAL_PREFIX ("~tidyup-partial-")
def is_atomic_dir(path: Path, cfg: Config) -> bool
    # path.name matches atomic.folder_names (fnmatch, casefold) OR any entry name in os.listdir(path)
    # matches atomic.markers (fnmatch, casefold). OSError -> False. Source roots themselves are never atomic.
def find_atomic_ancestor(path: Path, cfg: Config) -> Path | None
    # nearest ancestor (excluding path itself) that is_atomic_dir, checking from path.parent upward and
    # stopping at (and excluding) the source root / folders["home"] containing it, or the filesystem root.
def assert_not_protected(path: Path) -> None
    # config-free guard used by fsops: raise UnsafePathError if is_protected(path) or (path exists and is_hidden_or_system(path))
def check_source(path: Path, cfg: Config, *, is_dir: bool) -> None
    # raise UnsafePathError if assert_not_protected fails, is_excluded, find_atomic_ancestor(path) is not None,
    # or (is_dir and not is_atomic_dir(path)) — only atomic folders may be moved as directories.
def check_destination(path: Path, cfg: Config) -> None
    # raise UnsafePathError if is_protected(path) or find_atomic_ancestor(path) is not None or is_excluded(path)
```
(`safety` imports `fsops.PARTIAL_PREFIX` lazily or duplicates the constant `PARTIAL_PREFIX = "~tidyup-partial-"`; duplicate it to avoid a cycle.)

#### fsops.py (P2-T2)
```python
PARTIAL_PREFIX = "~tidyup-partial-"

@dataclass(frozen=True)
class MoveResult:
    src: Path; dst: Path; cross_volume: bool; is_dir: bool; size: int; sha256: str | None

def sha256_file(path: Path, *, is_cloud_only: bool = False) -> str
    # winutil.assert_local(path, is_cloud_only) first; 1 MiB chunks; lock errors -> FileLockedError
def reserve_key(path: Path) -> str          # winutil.norm_key(path).casefold()
def unique_path(dst: Path, reserved: set[str] | None = None) -> Path
    # dst if not os.path.lexists and key not in reserved; else "stem (2)suffix", "(3)"... (Path.stem/suffix,
    # directories use the full name). Adds the chosen key to `reserved` when given.
def ensure_dir(path: Path, journal: Journal | None = None, *, dry_run: bool = False) -> list[Path]
    # creates missing directories top-down with os.mkdir (never makedirs exist_ok on existing files);
    # journal.mkdir(each created); returns created dirs
def move_file(src: Path, dst: Path, *, journal: Journal | None = None, row_id: str | None = None,
              action: str = "move", is_cloud_only: bool = False, adopt_identical: bool = False,
              dry_run: bool = False) -> MoveResult
def move_tree(src_dir: Path, dst_dir: Path, *, journal: Journal | None = None, row_id: str | None = None,
              action: str = "move", adopt_identical: bool = False, dry_run: bool = False) -> MoveResult
def trash(path: Path, *, journal: Journal | None = None, row_id: str | None = None, dry_run: bool = False) -> None
    # safety.assert_not_protected; winutil.send_to_recycle_bin; journal.trash
def remove_empty_dir(path: Path) -> bool    # os.rmdir only; False if not empty/missing. Used by undo.
```
`move_file` algorithm (exact):
1. `safety.assert_not_protected(src)`; `safety.assert_not_protected(dst.parent)`; src must be an existing regular file.
2. `final = dst` if `adopt_identical` and `dst` exists with equal size and SHA-256 (then skip to step 5 "remove source"); otherwise `final = unique_path(dst)`.
3. `ensure_dir(final.parent, journal)`.
4. Same volume (`winutil.same_volume(src, final.parent)`): `_no_clobber_rename(src, final)` = raise `FileExistsError` if `os.path.lexists(final)`, then `os.rename` (never `os.replace`). Windows `os.rename` also refuses existing targets.
5. Cross volume: refuse with `CloudOnlyError` if `is_cloud_only or winutil.is_cloud_only(src)`. `h = sha256_file(src)`; record `(size, mtime_ns)` of src; temp = `final.parent / (PARTIAL_PREFIX + final.name)`; if temp exists `_discard_partial(temp)`; `shutil.copy2(long(src), long(temp))`; `h2 = sha256_file(temp)`; mismatch → `_discard_partial(temp)`, raise `VerifyError` (source untouched); `_no_clobber_rename(temp, final)`; then `_remove_verified_source(src, size, mtime_ns)` which re-stats src and only `os.unlink`s it if size and mtime are unchanged (else raise `VerifyError`, leaving both copies).
6. `journal.move(...)` only after success (`sha256=h` for cross-volume, else None).
7. Any `OSError` with `winutil.is_lock_error` → `FileLockedError`; temp partials are discarded on every failure path.
8. `dry_run`: compute `final` (with `unique_path`) and return `MoveResult` without touching disk.

`move_tree`: same volume → `_no_clobber_rename(src_dir, unique_path(dst_dir))`. Cross volume → pre-walk all files (including hidden ones inside the unit); any cloud-only → `CloudOnlyError` before copying anything; mirror dirs; per file copy to partial + verify + rename (or adopt identical existing file when `adopt_identical`); after **all** files verified, remove source files via `_remove_verified_source`, then source dirs bottom-up via `os.rmdir`; `shutil.copystat` on dirs. One journal `move` record with `is_dir=true`, `size`=total bytes, `sha256=None`.

#### extract.py (P2-T3)
```python
IMAGE_EXTS = frozenset({".jpg",".jpeg",".png",".gif",".bmp",".webp",".tif",".tiff"})   # readable by Pillow

@dataclass(frozen=True)
class ImageInfo:
    width: int; height: int
    camera_make: str | None; camera_model: str | None
    exif_datetime: datetime | None    # ExifIFD 0x8769 tag 36867 DateTimeOriginal, fallback IFD0 306 DateTime
    phash: str | None                 # str(imagehash.phash(img)) when want_phash

def read_image_info(path: Path, *, is_cloud_only: bool, want_phash: bool = True) -> ImageInfo | None
def pdf_text(path: Path, *, is_cloud_only: bool, max_pages: int = 2, max_chars: int = 20000) -> str
def docx_text(path: Path, *, is_cloud_only: bool, max_chars: int = 20000) -> str
def thumbnail_data_uri(path: Path, *, is_cloud_only: bool, max_px: int = 160) -> str | None   # "data:image/jpeg;base64,..."
```
Every function first checks `is_cloud_only or winutil.is_cloud_only(path)` and, if so, returns `None`/`""` WITHOUT opening the file (as built in P2-T3; a cloud-only file is therefore indistinguishable from a corrupt one — callers must check `is_cloud_only` themselves before calling and must not interpret an empty result as "no text" for cloud-only files). Corrupt/unsupported files return `None`/`""`; lock errors → `FileLockedError`. Files opened with `open(winutil.long_path(path), "rb")` inside `with`, passed as file objects to Pillow/pypdf/python-docx.

#### dupes.py (P2-T4) — pure functions over records
```python
@dataclass
class DuplicateGroup:
    sha256: str; keeper: FileRecord; duplicates: list[FileRecord]      # duplicates sorted by path
@dataclass
class VersionChain:
    key: str; current: FileRecord; older: list[FileRecord]             # current = newest modified
@dataclass
class NearDuplicateGroup:
    members: list[FileRecord]; max_distance: int                       # members sorted by path

def exact_duplicate_groups(records: Sequence[FileRecord], cfg: Config) -> list[DuplicateGroup]
    # entry_type file, not cloud-only, sha256 set, size >= rules.duplicates.min_size; group by sha256; >=2 members
def choose_keeper(records: Sequence[FileRecord], cfg: Config) -> FileRecord
    # min by key: (location rank of cfg.folder_containing(path) in rules.duplicates.location_priority
    # (unknown = len(list)), 1 if stem matches any version_chains pattern else 0, created, len(str(path)), str(path).casefold())
def normalize_version_stem(stem: str, patterns: Sequence[str]) -> str
    # casefold; repeatedly re.sub each pattern (IGNORECASE) with "" until stable; collapse [\s_-]+ to " "; strip
def version_chains(records: Sequence[FileRecord], cfg: Config, exclude: set[Path] = frozenset()) -> list[VersionChain]
    # files not in exclude; key = f"{norm_key(parent)}|{normalized stem}|{ext}"; group size >= 2 and at least one
    # member's normalized stem differs from its casefolded stem
def near_duplicate_groups(records: Sequence[FileRecord], cfg: Config) -> list[NearDuplicateGroup]
    # records with phash; hamming distance <= rules.near_duplicates.max_distance; union-find over pairs found via a
    # BK-tree; groups whose members all share one sha256 are dropped (exact dups); [] if enabled is false
```
All returned lists sorted deterministically (by keeper/current/first member path).

#### rules.py (P3-T2)
```python
RULE_NAMES = ("atomic_folder","exact_duplicate","installer","installer_old","archive","screenshot","photo",
              "video","pdf","document","unsorted","already_in_place","already_filed",
              "cloud_only_leave_onedrive","scan_error","migrate")

@dataclass
class RuleContext:
    cfg: Config
    now: datetime

@dataclass
class Proposal:
    kind: str                     # "file" | "review_delete" | "skip"
    logical_dir: str | None       # POSIX logical dir for Config.resolve_destination; None for skip
    new_name: str | None          # None = keep the current file name
    rule: str                     # one of RULE_NAMES
    confidence: Confidence
    notes: list[str] = field(default_factory=list)

@dataclass
class CategoryMatch:
    best: str | None                         # category name or None
    text_hits: dict[str, list[str]]          # category -> distinct keywords found in text
    filename_hits: dict[str, list[str]]      # category -> distinct keywords found in filename

def classify(record: FileRecord, ctx: RuleContext) -> Proposal       # entry_type FILE only
def screenshot_score(record: FileRecord, ctx: RuleContext) -> tuple[int, list[str]]
def date_from_filename(name: str) -> datetime | None
def photo_date(record: FileRecord) -> tuple[datetime, str]            # source in {"exif","filename","modified"}
def match_categories(text: str, filename: str, cfg: Config) -> CategoryMatch
def date_from_text(text: str, order: str = "MDY", now: datetime | None = None) -> datetime | None
def slugify(text: str, max_len: int = 40) -> str
def pdf_filename(date: datetime, category: str, description: str, cfg: Config) -> str
def is_in_target_structure(path: Path, cfg: Config) -> bool
```
Classification order inside `classify` (first match wins):
1. **Installer** (ext in `rules.installers.extensions`, path within any `only_in` folder): age = `now - modified` > `review_delete_after_days` → kind `review_delete`, logical `_Review_Delete`, rule `installer_old`, confidence low; else kind file, `Installers`, rule `installer`, high.
2. **Archive** (same pattern) → `Archives`, rule `archive`, high.
3. **Screenshot**: ext in `models.TYPE_GROUPS["Images"]` and `screenshot_score >= min_score` → `Pictures/Screenshots_Review`, rule `screenshot`, high if score ≥ `high_score` else medium; notes = `"screenshot score N: " + ", ".join(reasons)`. Signals: name starts with a prefix (casefold) +3; within `folders.screenshots` +3; `.png` +1; image readable and no camera make/model +1 (not counted for cloud-only); exact `WxH` (either orientation) in `resolutions` +2; else portrait and long/short within tolerance of a phone ratio +1; camera make/model present −3.
4. **Photo**: ext in `rules.photos.extensions`, or any image with camera make/model → `Pictures/Photos/{yyyy}/{yyyy}-{mm}` from `photo_date`: exif → high, filename → high, modified → medium; rule `photo`; note `"date from <source>"`.
5. **Video**: ext in `rules.videos.extensions` → `Videos/{yyyy}`; filename date → high, modified → medium; rule `video`.
6. **PDF**: cloud-only → treated like scanned (no read). Else `text = extract.pdf_text(...)`. Text present: `m = match_categories(text, name)`; high when `len(text_hits[best]) >= high_min_hits` and `>= 2 *` the runner-up's text hits → `logical = "Documents/<best>"`, `new_name = pdf_filename(date_from_text(text) or modified, best, description)`, rule `pdf`, high; otherwise `_Unsorted/PDFs`, keep name, medium if any hit else low (notes name the guessed category). No text (scanned) or cloud-only: filename hit → `Documents/<best>`, keep name, medium; none → `_Unsorted/PDFs`, low; note `"no text layer (scanned?)"` or `"cloud-only: classified by name only"`.
   Description: `slugify(stem)` unless the stem is generic (`^(scan|scanned|document|doc|file|untitled|img|image|pdf)?[\s_-]*\d*$`, case-insensitive) → slugify first line of text having ≥3 letters (first 6 words); empty → `"Document"`. `slugify`: NFKD→ASCII, words of `[A-Za-z0-9]+` joined by `-`, cut at word boundary ≤ max_len.
7. **Document**: ext in `text_extensions` (local: `extract.docx_text`) or `filename_only_extensions` → `match_categories`; docx text hits meeting the PDF high test → `Documents/<best>`, keep name, high; any hit (text or filename) → `Documents/<best>`, medium; none → `_Unsorted/Documents`, low. Rule `document`. Documents are never renamed.
8. **Fallback** → `_Unsorted/<type_group(ext)>`, rule `unsorted`, low.

`match_categories`: casefold, collapse whitespace; filename = stem with `[_\-.]+` → space; keyword regex `(?<![a-z0-9])<escaped kw>(?![a-z0-9])`; `best` = category with most text hits, then most filename hits, ties by config order; `Other` never matches. `date_from_text`: first plausible (1990 ≤ year ≤ now.year+1) of ISO `YYYY-MM-DD`, numeric `MM/DD/YYYY` (or `DD/MM/YYYY` when order DMY), `15 March 2024`, `March 15, 2024`, `Mar 15 2024`. `date_from_filename`: `(?<!\d)(20\d{2}|19\d{2})[-_]?(\d{2})[-_]?(\d{2})(?!\d)` with valid month/day (covers IMG_20240714_, PXL_20240714_, VID_, Screenshot_20240601-, WhatsApp Image 2024-07-14). `is_in_target_structure`: path within any resolved structure folder (`Documents/<any category>`, Photos root, Screenshots_Review, Videos/<4 digits>, Installers, Archives, _Unsorted, _Duplicates, _Review_Delete).
Cloud-only records: never call `extract`; append note `"cloud-only"` to every proposal.

#### scan.py (P3-T1)
```python
@dataclass
class ScanResult:
    records: list[FileRecord]
    skipped: dict[str, int]            # reason -> count: "excluded","protected","hidden","symlink","access denied","locked"
    errors: list[tuple[Path, str]]

def walk_sources(cfg: Config) -> Iterator[FileRecord]     # stat-only pass, no content reads
def scan_sources(cfg: Config, *, show_progress: bool = True) -> ScanResult
def summarize(records: Sequence[FileRecord]) -> dict      # {"by_type": {group: (count, bytes)}, "by_source": {label: (count, bytes)}, "cloud_only": n, "atomic_folders": n, "total": (count, bytes)}
def command(cfg: Config, args: argparse.Namespace) -> int
```
Walk: explicit stack with `os.scandir(long_path(dir))`, top-down, sorted by name. For each entry: skip symlinks and junctions (`entry.is_symlink()`, `entry.is_junction()` when available) — do NOT use the REPARSE_POINT attribute alone (OneDrive placeholders carry it); `safety.is_excluded` → skip; attrs = `winutil.get_file_attributes(path, st)`; hidden/system → skip. Directory: `safety.is_atomic_dir` → yield one `ATOMIC_FOLDER` record (size + file_count from a stat-only walk inside, dates from the dir, `atomic_root=path`), do not descend; else push. File: yield `FILE` record with `is_cloud_only=winutil.is_cloud_only_attrs(attrs)`. `PermissionError` on a dir → skipped "access denied", continue.
`scan_sources`: walk; group local files by size (≥ `rules.duplicates.min_size`), `fsops.sha256_file` for groups of ≥2; `extract.read_image_info` for local files with ext in `extract.IMAGE_EXTS`; `FileLockedError` → record `error="locked: ..."`, count, continue. Progress bars via `console.progress`.
`command`: scan, print summary tables (by type, by source, cloud-only count, atomic folders, skipped reasons), write `cfg.inventory_path` unless `--dry-run`, print the next step (`tidyup plan`).

#### plan.py (P4-T1)
```python
@dataclass
class PlanResult:
    rows: list[ManifestRow]
    warnings: list[str]                 # human-readable, includes every OneDrive exit
    onedrive_exits: list[ManifestRow]
    counts: dict[str, int]              # "<action>/<confidence>" -> n, plus "approved"

def build_manifest(cfg: Config, records: Sequence[FileRecord], *, now: datetime | None = None) -> PlanResult
def command(cfg: Config, args: argparse.Namespace) -> int
```
Algorithm: (1) `groups = dupes.exact_duplicate_groups`, non-keepers → map path→keeper; `chains = dupes.version_chains(exclude=non-keepers)`; `near = dupes.near_duplicate_groups`. (2) Records sorted by `str(path).casefold()`; `reserved: set[str] = set()`. Per record:
- `error` non-empty → skip, rule `scan_error`.
- `ATOMIC_FOLDER`: `dest_root` None → skip, rule `atomic_folder`, high, note "atomic folder: left as one unit". Else move to `dest_root / <source label> / <path relative to its source root>`, medium.
- Non-keeper duplicate → action `duplicate`, destination `resolve_destination("_Duplicates", src) / name`, rule `exact_duplicate`, high, note `"duplicate of <keeper path> (sha256 <first 12 hex>)"`.
- Else `p = rules.classify(rec, RuleContext(cfg, now))`. If `p.confidence != high` and `rules.is_in_target_structure(src)` → skip, `already_filed`. `kind == "skip"` → skip. `kind == "review_delete"`: delete_mode review → destination `resolve_destination(p.logical_dir, src)/name`, recycle_bin → destination None, note "will be sent to the Recycle Bin". `kind == "file"`: `dest = resolve_destination(p.logical_dir, src) / (p.new_name or src.name)`; same dir+name → skip `already_in_place`; same dir → `rename`; same name → `move`; else `move_rename`.
- OneDrive: `cfg.in_onedrive(src)` and destination not in OneDrive → cloud-only: skip, rule `cloud_only_leave_onedrive`; local: note prefixed `"WARNING: moves this file OUT of OneDrive (it will no longer be in the cloud)"`, approved forced N, added to `onedrive_exits`.
- Collision: for any row with a destination, `destination = fsops.unique_path(dest, reserved)`; if changed, note `"name collision: will be saved as '<name>'"`.
- Version chain `older` members: approved forced N, note `"likely old version of <current path>"`. Near-dup members: note `"near-duplicate group G<n> (see report)"`. Cloud-only: note `"cloud-only"`.
- `approved = confidence == high` unless forced N; skip rows always N. `id` = 1..n in row order.
`command`: requires `cfg.inventory_path` (else `TidyupError("No inventory found. Run 'tidyup scan' first.")`), writes `cfg.manifest_path` unless `--dry-run`, prints counts table (action × confidence, approved), a prominent block listing OneDrive exits (up to 20 + "and N more"), and next steps.

#### report.py (P3-T5)
```python
def build_report(cfg: Config, records: Sequence[FileRecord], rows: Sequence[ManifestRow] | None) -> str
def command(cfg: Config, args: argparse.Namespace) -> int     # writes cfg.report_path unless --dry-run; --open uses webbrowser
```
Single self-contained HTML string: inline `<style>`, no external URLs (no `http://`/`https://` in any `src`/`href`), no JS required. Every path/notes value `html.escape`d. Sections with these exact `id`s: `summary` (files, total size, cloud-only count, atomic folders, rows by action and confidence, approved count, OneDrive warnings), `tree` (proposed folder tree from non-skip destinations, nested `<details>` with file counts, top two levels open), `screenshots` (grid of rows with rule `screenshot`: thumbnail via `extract.thumbnail_data_uri` for local files, grey "cloud-only — not downloaded" box otherwise; filename, confidence, notes), `duplicates` (each `DuplicateGroup` as a side-by-side flex row: keeper card marked "KEEP", duplicate cards; thumbnails for images), `near-duplicates`, `versions` ("Likely old versions": each `VersionChain`, current + older), `onedrive` (rows whose notes contain the OneDrive WARNING). Thumbnails capped at `report.max_thumbnails` total; beyond the cap show filename only. `rows` None (no manifest yet) → sections that need it show "Run tidyup plan to see proposed changes."

#### apply.py (P3-T3)
```python
@dataclass
class ApplyResult:
    run_id: str | None; log_path: Path | None
    moved: int; trashed: int; skipped: int; errors: int
    dry_run: bool

def select_rows(rows: Sequence[ManifestRow], cfg: Config, *, limit: int | None = None, only_rule: str | None = None,
                only_folder: str | None = None, actions: set[Action] | None = None,
                min_confidence: Confidence | None = None) -> list[ManifestRow]
    # approved and action != skip; rule equals only_rule (casefold); source within cfg.folders[only_folder]
    # (or the literal path); action in actions; confidence rank >= min_confidence rank (compare by rank low=0 < medium=1 < high=2, NEVER by string); keep id order; then first `limit`
def apply_rows(cfg: Config, rows: Sequence[ManifestRow], *, kind: str = "apply", manifest_path: Path | None = None,
               dry_run: bool = False) -> ApplyResult
def command(cfg: Config, args: argparse.Namespace) -> int
```
Per row: source missing → skip "source no longer exists"; `is_dir = source is a directory`; `safety.check_source(src, cfg, is_dir=is_dir)`; destination None/empty for a move-type row → error; `safety.check_destination(dst, cfg)`; dir destination inside its own source → error. `review_delete` with `delete_mode == "recycle_bin"` → `fsops.trash`; otherwise (`move`, `rename`, `move_rename`, `duplicate`, `review_delete` in review mode — destination blank → `resolve_destination("_Review_Delete", src)/name`) → `fsops.move_file` (`is_cloud_only=winutil.is_cloud_only(src)`) or `fsops.move_tree`. `UnsafePathError`/`CloudOnlyError`/`FileLockedError` → `journal.skip`, continue; `VerifyError`/other `OSError` → `journal.error`, continue. `KeyboardInterrupt` → `journal.finish(counts)` then re-raise. No journal in dry run.
`command`: manifest = `--manifest` or `cfg.manifest_path` (missing → TidyupError "run tidyup plan"); `select_rows(limit, only_rule, only_folder)`; print count and first 20 operations; if any `review_delete` and recycle_bin mode, warn that Recycle Bin items cannot be restored by `tidyup undo`; `console.confirm("Apply N changes?", assume_yes=args.yes)` unless dry run (refuse → exit 1, nothing done); print summary and `To reverse this run: tidyup undo <run_id>`.

#### undo.py (P3-T4)
```python
@dataclass
class UndoResult:
    undone_run_id: str; run_id: str | None; log_path: Path | None
    restored: int; skipped: int; errors: int; manual: list[Path]     # trashed items to restore by hand
    dry_run: bool

def undo_run(cfg: Config, run: RunLog, *, dry_run: bool = False) -> UndoResult
def command(cfg: Config, args: argparse.Namespace) -> int   # args.run_id (optional), args.list
```
Process `run.ops` in reverse: `move` → if `dst` missing: skip; target = `src`, or `fsops.unique_path(src)` with a warning if `src` is occupied; `fsops.move_file(dst, target)` / `move_tree` for `is_dir` (cross-volume handled by fsops: copy→verify→remove). `mkdir` → `fsops.remove_empty_dir` (non-empty: leave, note). `trash` → append to `manual`. Writes its own journal (`kind="undo"`, `meta.undoes=<run_id>`) so an undo can itself be undone; then `journal.mark_undone(run, new_run_id)`. Already undone run → TidyupError unless it is explicitly named (then warn and continue). `--list` prints run_id, kind, started, op count, finished/undone status. Works on unfinished (interrupted) runs.

#### migrate.py (P3-T6)
```python
@dataclass
class MigratePlan:
    to_root: Path
    rows: list[ManifestRow]          # action move (or skip), rule "migrate", confidence high, approved Y for moves
    required_bytes: int              # bytes of non-skip rows crossing volumes + margin
    free_bytes: int

def build_migration(cfg: Config, to_root: Path) -> MigratePlan
def check_space(plan: MigratePlan) -> None      # InsufficientSpaceError with both sizes in human units
def run_migration(cfg: Config, rows: Sequence[ManifestRow], to_root: Path, *, dry_run: bool = False) -> ApplyResult
def command(cfg: Config, args: argparse.Namespace) -> int   # args.to, args.manifest, args.yes
```
`build_migration`: `to_root` must exist (TidyupError otherwise). For each `migrate.folders` entry (folder key or `{key}/sub` template; missing → silently skipped): folders inside OneDrive skipped with a warning unless `include_onedrive`. Walk like scan (skip excluded/protected/hidden; atomic dirs = one row); destination `to_root / <display name> / <relative path>` where display name is `Documents|Pictures|Videos|Downloads|Desktop` for keys, else the folder's own name. Cloud-only files → skip rows with note "cloud-only: not migrated (would download)". `required_bytes = sum(sizes) + free_space_margin_mb MiB`; `free_bytes = winutil.free_space(to_root)`.
`command`: `--dry-run` → build, write `cfg.migrate_manifest_path` (this is the review step), print totals and space check result (never moves). Real run → manifest = `--manifest` or `cfg.migrate_manifest_path`; missing → TidyupError "Run 'tidyup migrate --to X --dry-run' first and review migrate_manifest.csv"; re-run `check_space` for remaining rows (refuse → exit 1, nothing moved); confirm; `run_migration`.
`run_migration`: resumable — if `list_runs` has an unfinished `kind="migrate"` run with `meta.to == str(to_root)`, `Journal.resume` it; rows whose source no longer exists and whose destination exists → count as done; every move uses `adopt_identical=True` so a destination copied before the interruption is verified and adopted, never duplicated. Uses `fsops.move_file`/`move_tree` (copy→verify→remove when cross-volume). Ends with instructions on pointing Windows' Documents/Pictures/Videos "Location" tab at the new folders.

#### schedule.py — REMOVED
No scheduled task (user runs tidyup as needed). Do not create `tidyup/schedule.py`; no code may call `schtasks`.

#### upkeep.py (P4-T2)
```python
def run_upkeep(cfg: Config, *, dry_run: bool = False) -> ApplyResult
def command(cfg: Config, args: argparse.Namespace) -> int
```
`ucfg = cfg.with_sources(cfg.upkeep["sources"])`; `scan.scan_sources(ucfg, show_progress=False)`; `plan.build_manifest(ucfg, records)`; write the manifest to `logs/upkeep_manifest_<YYYYMMDD-HHMMSS>.csv` (the reviewable record); `apply.select_rows(rows, ucfg, actions={Action(a) for a in upkeep.actions}, min_confidence=HIGH)` — `review_delete` and `duplicate` can never be selected; `apply.apply_rows(..., kind="upkeep")`; append one summary line to `logs/upkeep.log`. Confirmation (in `command`, same helper/behaviour as apply): print the number of selected rows and the upkeep manifest path, then ask `Proceed? [y/N]` unless `args.yes`; non-interactive without `--yes` → refused, exit 1, nothing moved. `--dry-run` prints the planned operations and moves nothing.

#### cli.py (P2-T1)
```python
def build_parser() -> argparse.ArgumentParser
def main(argv: Sequence[str] | None = None) -> int
```
Grammar: `python -m tidyup [--config PATH] [--dry-run] [--debug] [--version] COMMAND ...` where `--config`, `--dry-run`, `--debug` are also accepted after the command (shared parent parser with `default=argparse.SUPPRESS` on subparsers). Commands and args (attribute names exact): `scan`; `plan`; `report [--open]`; `apply [--manifest PATH] [--limit N] [--only-rule NAME] [--only-folder NAME] [--yes]`; `undo [run_id] [--list]`; `migrate --to PATH [--manifest PATH] [--yes]`; `upkeep [--yes]`. Namespace always has `command, config, dry_run, debug` (+ the command's own: `open, manifest, limit, only_rule, only_folder, yes, run_id, list, to`).
Config: `--config` given → must exist else ConfigError; else `config.find_default_config()`; else packaged defaults. Dispatch lazily: `importlib.import_module(f"tidyup.{name}").command(cfg, args)`.
Errors: `TidyupError` → `console.error(msg)`, exit 1. `KeyboardInterrupt` → "Interrupted. Changes made so far are logged and can be reversed with 'tidyup undo'.", exit 130. Other exceptions → friendly one-liner + traceback written to `<work_dir>/logs/crash_<YYYYMMDD-HHMMSS>.txt` (path printed), exit 1; `--debug` re-raises. argparse errors exit 2.

---

## 2. Safety design

| # | Spec rule | Enforcement in code | Proving test(s) |
|---|---|---|---|
| S1 | Never hard-delete | Only `fsops._remove_verified_source` (after SHA-256 verify), `fsops._discard_partial` (our own temp copies), `fsops.remove_empty_dir` (`os.rmdir`, empty dirs created by us) may delete; only `winutil.send_to_recycle_bin` calls send2trash. "Delete" = move to `_Review_Delete` or `fsops.trash`. | `test_static_safety.py` (AST allowlist over `tidyup/*.py`); `test_apply.py::test_review_delete_moves_not_deletes`; `test_e2e.py::test_file_count_preserved` |
| S2 | Never touch system folders / hidden / system files | `safety.is_protected`, `is_hidden_or_system`, `is_excluded` in `scan.walk_sources`; `safety.check_source/check_destination` in apply; `safety.assert_not_protected` inside every fsops mutator (defense in depth). | `test_safety.py`; `test_scan.py::test_protected_hidden_excluded_not_inventoried`; `test_apply.py::test_refuses_protected_and_hidden_rows`; `test_fsops.py::test_refuses_protected` |
| S3 | Atomic folders moved whole, never reorganized | Scan emits one `atomic_folder` record and never descends; plan skips/moves it whole; `safety.check_source` refuses any path with an atomic ancestor and any directory that is not atomic; `check_destination` refuses destinations inside atomic folders; `fsops.move_tree` moves the unit including hidden contents. Markers and names configurable. | `test_scan.py::test_atomic_folders_single_row`; `test_apply.py::test_refuses_row_inside_atomic_folder`; `test_e2e.py::test_atomic_folders_never_split` |
| S4 | Manifest review; apply only approved rows | apply/upkeep/migrate execute only `ManifestRow`s read from a CSV with `approved=True`; defaults high→Y else N; forced N for OneDrive exits, version chains. Migrate real run requires a dry-run manifest. | `test_apply.py::test_only_approved_rows`; `test_models.py::test_read_manifest_excel_variants`; `test_migrate.py::test_requires_manifest` |
| S5 | Undo log; undo fully reverses | fsops writes `mkdir`/`move`/`trash` records (fsync'd) only after each success; undo reverses in reverse order and removes created dirs. | `test_undo.py`; `test_e2e.py::test_plan_apply_undo_restores_tree_byte_for_byte` |
| S6 | Never overwrite | `fsops.unique_path` at plan time (reserved set, case-insensitive) and again at execution; `_no_clobber_rename` checks `lexists` then `os.rename` (never `os.replace`/`shutil.move`); copies go to a partial temp name. | `test_fsops.py::test_unique_path_suffixes`, `::test_never_overwrites_existing`; `test_plan.py::test_collision_reserved`; `test_e2e.py` collision pair |
| S7 | Preserve timestamps | same-volume `os.rename`; cross-volume `shutil.copy2` (+`copystat` for dirs). | `test_fsops.py::test_mtime_preserved_same_and_cross_volume` |
| S8 | Cross-drive = copy → SHA-256 verify → remove | `fsops.move_file/move_tree` cross-volume branch; source removal only in `_remove_verified_source` after hash match and unchanged size/mtime. | `test_fsops.py::test_cross_volume_verifies_before_remove` (call-order spy), `::test_corrupt_copy_keeps_source`; `test_migrate.py::test_verify_before_remove` |
| S9 | Long paths | every OS call wrapped in `winutil.long_path`; records store stripped paths. | `test_winutil.py::test_long_path_formats` (is_windows mocked); `test_fsops.py::test_uses_long_path` (spy); fixture includes a >260-char path moved+undone in e2e |
| S10 | Locked files skipped, logged, continue | `winutil.is_lock_error` → `FileLockedError`; scan records `error="locked..."`; apply/undo/migrate journal `skip` and continue. | `test_scan.py::test_locked_file_continues`; `test_apply.py::test_locked_file_skipped_and_logged` |
| S11 | Never read cloud-only contents | `winutil.assert_local` at the top of every `extract` function and `fsops.sha256_file`; scan/rules/report check `is_cloud_only` before calling them; cross-volume copy of placeholders refused; plan skips cloud-only files that would leave OneDrive. | `conftest.open_spy` + `test_e2e.py::test_cloud_only_never_opened` (scan, plan, report, apply, upkeep); `test_extract.py::test_refuses_cloud_only`; `test_scan.py::test_cloud_only_marked_not_hashed` |
| S12 | Warn on moves out of OneDrive | plan note + forced N + printed warning block + report `onedrive` section; in-place resolution keeps OneDrive files in OneDrive. | `test_plan.py::test_onedrive_exit_warning`; `test_config.py::test_resolve_destination_onedrive_guard` |
| S13 | Upkeep: only high-confidence move/rename, no deletes | `upkeep` uses `select_rows(actions=move/rename/move_rename, min_confidence=HIGH)`. | `test_upkeep.py::test_only_high_move_rename` |
| S14 | Migrate refuses without space | `check_space` before any move, in both dry run and real run. | `test_migrate.py::test_refuses_insufficient_space` |

`tests/test_static_safety.py` allowlist (module, function) → allowed calls: (`fsops`, `_remove_verified_source`) → `os.unlink`; (`fsops`, `_discard_partial`) → `os.unlink`; (`fsops`, `remove_empty_dir`) → `os.rmdir`; (`fsops`, `move_tree`) → `os.rmdir`; (`winutil`, `send_to_recycle_bin`) → `send2trash`. Also asserts no `os.replace`, `shutil.move`, `shutil.rmtree` anywhere, and that `shutil.copy*`/`os.rename` appear only in `fsops`.

---

## 3. Fake file tree (contract for P1-T5 and every test author)

`tests/fixtures/fake_tree.py`:
```python
@dataclass
class FakeTree:
    root: Path
    folders: dict[str, Path]        # home, documents, desktop, downloads, pictures, videos, onedrive, screenshots
    files: dict[str, Path]          # handles below
    cloud_only: list[Path]
    hidden: list[Path]
    protected: list[Path]
    excluded: list[Path]
    atomic_dirs: list[Path]
    duplicate_groups: list[list[Path]]
    version_chain: list[Path]
    collision_pair: tuple[Path, Path]
    long_path_file: Path
    def config_overrides(self) -> dict
        # {"folders": {k: str(v)...}, "sources": ["documents","desktop","downloads","pictures","videos","onedrive"],
        #  "work_dir": str(root/"tidyup_data"), "destination": {"root": None}}
    def all_files(self) -> list[Path]

def build_fake_tree(root: Path, now: datetime | None = None) -> FakeTree
def snapshot(root: Path) -> dict[str, tuple[str, int] | None]
    # relative POSIX path -> (sha256, int(st_mtime)) for files, None for directories; includes hidden entries
```
Layout under `root/home` (`folders.home`); `onedrive = home/OneDrive`; `screenshots = pictures/Screenshots`; work dir `root/tidyup_data` (outside home). Every file gets a deterministic mtime via `os.utime` (default 2024-01-10 12:00 local, unless stated). Uses its own `_lp()` helper for the `\\?\` prefix on Windows (must not import tidyup). Handles (`files[...]`):
- `invoice_pdf` Documents/invoice_march.pdf — text "INVOICE / Invoice number: INV-10023 / Invoice date: March 15, 2024 / Subtotal: $100.00 / Amount paid: $120.00 / Thank you for your purchase" (→ Receipts, high, 2024-03-15).
- `statement_pdf` Downloads/estatement.pdf — "First National Bank / Statement period: 01/01/2024 - 01/31/2024 / Account number: ****1234 / Closing balance: $5,000.00 / Interest paid: $1.20" (→ Finance, high).
- `syllabus_pdf` Desktop/CS101.pdf — "CS 101 Course Syllabus / Fall semester 2023 / Professor: Dr. Smith / Assignments and grading / Lecture schedule" (→ School, high).
- `scanned_pdf` Documents/scan_lease_2022.pdf — image-only PDF (reportlab drawImage, no text) (→ Housing by filename, medium).
- `plain_pdf` Documents/notes.pdf — "Lorem ipsum dolor sit amet" (→ _Unsorted/PDFs, low).
- `agenda_docx` Documents/Meeting agenda.docx — "Agenda / Project kickoff meeting / Action items" (→ Work, high).
- `version_chain` Desktop/report v1.docx, report v2.docx, report final_FINAL.docx (distinct content; mtimes Jan 1/2/3 2024).
- `notes_txt_desktop` Desktop/notes.txt and `notes_txt_downloads` Downloads/notes.txt (different content) = `collision_pair` (both → _Unsorted/Documents/notes.txt).
- `screenshot_desktop` Desktop/Screenshot 2024-05-01 101500.png 1920x1080; `screenshot_capture` Pictures/Screenshots/Capture_001.png 2560x1440; `screenshot_phone` Pictures/Screenshots/Screenshot_20240601-120000.png 1080x2400; `existing_review_screenshot` Pictures/Screenshots_Review/Screenshot 2024-05-01 101500.png (different pixels → forces " (2)").
- `photo_exif` Downloads/IMG_20240714_101010.jpg (EXIF Make "Canon", Model "EOS 80D", DateTimeOriginal 2024:07:14 10:10:10), `photo_exif2` Pictures/DSC_0001.jpg (Make "NIKON", DateTimeOriginal 2023:01:02 09:00:00), `photo_dup` Downloads/DSC_0001 (1).jpg (byte copy of photo_exif2 → duplicate group with it), `photo_near` Pictures/DSC_0001_small.jpg (photo_exif2 resized to 90%, re-encoded → near-duplicate), `drawing_png` Pictures/drawing.png 500x500 (→ _Unsorted/Images).
- `budget_xlsx` Documents/budget.xlsx and `budget_copy` Desktop/budget (1).xlsx (identical bytes → duplicate group).
- `installer_old` Downloads/setup_tool.exe (mtime now−200 days), `installer_new` Downloads/new_app.msi (now−5 days), `archive_zip` Downloads/photos_backup.zip, `archive_7z` Downloads/old.7z.
- `video` Videos/VID_20230505_101010.mp4 (random bytes), `video_nodate` Videos/clip.mov.
- `misc` Downloads/data.bin (→ _Unsorted/Other).
- Atomic dirs: `git_project` Documents/Code/myapp/ with `.git/HEAD`, `.git/config`, `src/main.py`, `README.md`, `notes.txt`; `node_project` Desktop/webapp/ with `package.json`, `node_modules/x/index.js`; `app_folder` Documents/My Games/SomeGame/save.dat.
- Cloud-only (in `cloud_only`; real bytes on disk, attributes mocked): `cloud_pdf` OneDrive/Taxes 2023.pdf, `cloud_photo` OneDrive/IMG_20220101_120000.jpg, `cloud_screenshot` OneDrive/Screenshot_cloud.png. Local OneDrive file: `onedrive_letter` OneDrive/Personal letter.docx.
- `hidden`: Documents/.hidden_notes.txt and Desktop/secret.txt (both listed in `hidden`; the second is hidden only via mocked attributes).
- `protected`: Documents/AppData/cache.txt, Downloads/$Recycle.Bin/deleted.txt, Downloads/Program Files (x86)/app/tool.exe.
- `excluded`: Desktop/desktop.ini, Downloads/movie.crdownload.
- `long_path_file`: Documents/<"a"*60>/<"b"*60>/<"c"*60>/<"d"*60>/long name document.txt (> 260 chars from root).

`tests/conftest.py` fixtures (exact names): `fake_tree(tmp_path) -> FakeTree`; `cfg(fake_tree) -> Config` (`load_config(None, overrides=fake_tree.config_overrides())`); `mock_attrs(monkeypatch, fake_tree)` patches `winutil.get_file_attributes` → `CLOUD`(0x400000|0x1000) for `cloud_only`, `HIDDEN` for `hidden`, otherwise the real function (matching by `winutil.norm_key`); `cross_volume(monkeypatch)` patches `winutil.same_volume` → False; `open_spy(monkeypatch) -> list[str]` wraps `builtins.open`, `io.open`, `PIL.Image.open`, `pypdf.PdfReader`, `docx.Document` and records the normalized path of every file opened (call-through to the originals). Imports of `tidyup` modules happen inside fixture bodies.

---

## 4. Tasks

Format: owns = files the task creates/modifies (exclusive within its phase). Reads = may read. Run parallel tasks only after their listed dependencies are checked off.

### Phase 1 — Foundation
Order: P1-T1 → {P1-T2, P1-T3, P1-T5 in parallel} → P1-T4 → P1-T6.

#### - [x] P1-T1 Package skeleton, tooling, errors, console
- Goal: importable package and all module stubs so later tasks fill modules in parallel.
- Owns: `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `.gitignore`, `tidyup/__init__.py`, `tidyup/__main__.py`, `tidyup/errors.py`, `tidyup/console.py`, stubs `tidyup/{cli,scan,extract,rules,dupes,plan,fsops,apply,undo,migrate,report,upkeep}.py`, `tests/test_console.py`, `tests/test_skeleton.py`.
- Reads: SPEC, PLAN. Depends: none.
- Acceptance: stubs carry the exact §1.3 signatures/dataclasses (`raise NotImplementedError`), import Phase-1 types only under `TYPE_CHECKING`, every module imports cleanly; cli stub `main()` supports `--version` only; `python -m tidyup --version` prints `tidyup 0.1.0`; console functions per §1.3.
- Tests: import every `tidyup.*` module; `python -m tidyup --version` via subprocess; `human_size` table; `confirm` with assume_yes / non-TTY; `progress` yields all items.

#### - [x] P1-T2 Windows helper layer (parallel)
- Owns: `tidyup/winutil.py`, `tests/test_winutil.py`. Reads: errors.py. Depends: P1-T1.
- Acceptance: §1.3 winutil exactly; no other module needed; works on Linux and Windows.
- Tests: `long_path` formats with `is_windows` mocked True/False (drive, UNC, already-prefixed, short); `strip_long_path` round trip; cloud-only mask for each of the 3 bits; `assert_local` raises/doesn't; `is_hidden_or_system`; `is_lock_error` for PermissionError, winerror 32, EBUSY; `is_within`/`norm_key`; `same_volume` same dir True; `free_space` > 0; `known_folders` keys (non-Windows path; Windows branch with a fake `winreg` module injected); `onedrive_root` via env; `file_times` returns datetimes.

#### - [x] P1-T3 Data models, CSV I/O, undo journal (parallel)
- Owns: `tidyup/models.py`, `tidyup/journal.py`, `tests/test_models.py`, `tests/test_journal.py`. Reads: errors.py. Depends: P1-T1.
- Acceptance: §1.3 models + journal exactly; CSV opens in Excel (BOM, header); journal lines fsync'd.
- Tests: inventory and manifest round trip (all fields, None, unicode paths, commas/quotes in names); `read_manifest_excel_variants` (semicolon delimiter, cp1252, `yes`/`x`/`1`, blank lines, reordered/upper-case headers); invalid action → ManifestError with row number; `type_group`; journal create/write/finish/read; truncated last line tolerated; `new_run_id` uniqueness; `find_run` latest-not-undone and kind filter; `mark_undone`; `resume` continues seq.

#### - [x] P1-T4 Config loader + default config
- Owns: `tidyup/config.py`, `tidyup/default_config.yaml`, `config.yaml`, `tests/test_config.py`. Reads: winutil.py, errors.py, console.py. Depends: P1-T1, P1-T2.
- Acceptance: §1.3 config + schema; every key commented in plain English (aimed at a non-programmer); `config.yaml` byte-identical to `tidyup/default_config.yaml`.
- Tests: defaults load with `known_folders` mocked; file + overrides deep-merge; unknown key / bad delete_mode / bad YAML (line number) → ConfigError; `work_dir` relative to config dir; KFM de-dup (documents inside onedrive → one source, label onedrive); missing source dropped with warning; `resolve_destination` in place, with dest_root, `test_resolve_destination_onedrive_guard`; `folder_containing` most specific; `with_sources`; repo `config.yaml` identical to packaged default.

#### - [x] P1-T5 Fake file tree generator + shared fixtures (parallel)
- Owns: `tests/conftest.py`, `tests/fixtures/__init__.py`, `tests/fixtures/fake_tree.py`, `tests/test_fake_tree.py`. Reads: §3. Depends: P1-T1.
- Acceptance: §3 exactly (handles, layout, contents, mtimes); uses Pillow and reportlab; no `tidyup` import at module level; builds in < 5 s.
- Tests: every handle exists; PNG dimensions; EXIF Make/DateTimeOriginal readable with Pillow; invoice PDF text extractable with pypdf and scanned PDF has none; duplicate groups byte-identical; long path > 260 chars; `snapshot` stable across two calls and detects a one-byte change, a rename and an mtime change.

#### - [x] P1-T6 Safety layer + static safety test
- Owns: `tidyup/safety.py`, `tests/test_safety.py`, `tests/test_static_safety.py`. Reads: config.py, winutil.py, errors.py. Depends: P1-T2, P1-T4.
- Acceptance: §1.3 safety exactly; static test implements the §2 allowlist via `ast` and passes on the current tree (stubs included).
- Tests: `is_protected` for `C:\Windows\x`, `C:\Program Files (x86)\a`, `...\AppData\...`, `$Recycle.Bin`, `System Volume Information`, `ProgramData`, and negatives (`Documents\Windows notes.txt`); hidden via dot and via mocked attrs; exclusions names/paths/work_dir/partial prefix; atomic markers (`.git`, `*.sln`, `venv`) and folder names (`Visual Studio 2022`); `find_atomic_ancestor` stops at source root; `check_source`/`check_destination` raise UnsafePathError; static test catches a synthetic offending snippet (parse a string) and passes the real package.

### Phase 2 — Building blocks (all parallel; depend on Phase 1)

#### - [x] P2-T1 CLI
- Owns: `tidyup/cli.py`, `tests/test_cli.py`. Reads: config.py, console.py, errors.py, all stubs. Depends: Phase 1.
- Acceptance: §1.3 cli grammar; options accepted before and after the command; friendly errors and crash file.
- Tests: `--help` and each subcommand `--help` exit 0; namespace attributes per command; `--dry-run` in both positions; missing `--config` file → exit 1 with message, no traceback; dispatch calls `tidyup.<cmd>.command` (monkeypatched); TidyupError → exit 1; RuntimeError → exit 1 + crash file in work_dir/logs; `--debug` re-raises; KeyboardInterrupt → 130.

#### - [x] P2-T2 fsops (safe file operations)
- Owns: `tidyup/fsops.py`, `tests/test_fsops.py`. Reads: winutil.py, safety.py, journal.py, errors.py. Depends: Phase 1.
- Acceptance: §1.3 fsops algorithm exactly; journal records only after success; static safety test still passes.
- Tests: `test_unique_path_suffixes` (" (2)", " (3)", reserved set, case-insensitive, dirs); `test_never_overwrites_existing` (dst created between plan and move → " (2)"); same-volume move + journal; `test_cross_volume_verifies_before_remove` (`cross_volume` fixture; spy order: hash(temp) before unlink(src)); `test_corrupt_copy_keeps_source` (patch `shutil.copy2` to write wrong bytes → VerifyError, src intact, no partial, no dst); `test_mtime_preserved_same_and_cross_volume`; cross-volume cloud-only refused; `move_tree` same and cross volume incl. hidden `.git` files; `adopt_identical`; `test_uses_long_path` (spy on `winutil.long_path`); lock error → FileLockedError; `test_refuses_protected`; `ensure_dir` journals created dirs; `remove_empty_dir` refuses non-empty; dry run touches nothing; `trash` calls mocked `winutil.send_to_recycle_bin`.

#### - [x] P2-T3 extract (content readers)
- Owns: `tidyup/extract.py`, `tests/test_extract.py`. Reads: winutil.py, errors.py, fixtures. Depends: Phase 1.
- Acceptance: §1.3 extract; never opens a cloud-only file; corrupt files return None/"".
- Tests: image info for fixture photos (size, make, model, DateTimeOriginal) and screenshots (no camera); phash is 16 hex chars and near-duplicate distance ≤ 6; `pdf_text` invoice contains "Invoice number", scanned → ""; `docx_text`; thumbnail data URI prefix; `test_refuses_cloud_only` (flag and mocked attrs) with `open_spy` empty; corrupt JPEG/PDF → None/"".

#### - [x] P2-T4 dupes
- Owns: `tidyup/dupes.py`, `tests/test_dupes.py`. Reads: models.py, config.py, winutil.py. Depends: Phase 1.
- Acceptance: §1.3 dupes; deterministic output; 20,000 synthetic phashes grouped in < 30 s.
- Tests: exact groups ignore cloud-only/no-hash/zero-size; keeper prefers Documents over Downloads, clean name over "(1)", older created; `normalize_version_stem` table (`report v3`, `final_FINAL`, `Copy of x`, `x - Copy (2)`, `x (1)`, `x_v2`, `draft`); chains require ≥2 members in same dir/ext; current = newest; near-dup grouping with threshold and exact-dup exclusion; performance test.

#### ~~P2-T5 schedule~~ — REMOVED (tidyup is run as needed; no scheduled task)

#### - [x] P2-T6 GitHub Actions workflow
- Owns: `.github/workflows/tests.yml`. Reads: requirements files. Depends: P1-T1.
- Acceptance: on push and pull_request; matrix `os: [ubuntu-latest, windows-latest]`, `python-version: ["3.11", "3.12"]`, `fail-fast: false`; `actions/checkout@v4`, `actions/setup-python@v5` with pip cache; `pip install -r requirements-dev.txt`; `python -m pytest -q`. Valid YAML.
- Tests: none (YAML validated by loading it with `yaml.safe_load` in the builder's own check).

### Phase 3 — Commands (all parallel; depend on Phase 2)

#### - [ ] P3-T1 scan
- Owns: `tidyup/scan.py`, `tests/test_scan.py`. Reads: config, safety, winutil, fsops, extract, models, console. Depends: P2-T2, P2-T3.
- Acceptance: §1.3 scan; read-only (snapshot unchanged); inventory written to `cfg.inventory_path`.
- Tests: fixture scan with `mock_attrs`: every expected file present; `test_protected_hidden_excluded_not_inventoried`; `test_atomic_folders_single_row` (3 atomic rows, no inner rows, file_count/size correct); `test_cloud_only_marked_not_hashed` (is_cloud_only Y, sha256/image fields empty, `open_spy` has no cloud path); sha256 only for size collisions; image info filled; KFM de-dup (no double rows); `test_locked_file_continues` (patch `fsops.sha256_file` to raise FileLockedError for one path); summary counts; dry run writes nothing; tree snapshot unchanged.

#### - [ ] P3-T2 rules
- Owns: `tidyup/rules.py`, `tests/test_rules.py`. Reads: config, models, extract, winutil. Depends: P2-T3.
- Acceptance: §1.3 rules order and confidences; cloud-only never passed to extract.
- Tests: build `FileRecord`s from fixture files (via `extract.read_image_info`, `os.stat`) — do not use scan; screenshot scores for the three fixture screenshots (high) and drawing.png (< min); photo dates exif/filename/modified; video; installer old/new + archive only in Downloads; invoice/statement/syllabus → high with names matching `^\d{4}-\d{2}-\d{2}_(Receipts|Finance|School)_[A-Za-z0-9-]+\.pdf$`; scanned → Documents/Housing medium; plain → _Unsorted/PDFs low; docx Work high; txt → _Unsorted/Documents low; cloud PDF classified by name with `open_spy` empty; `date_from_text` and `date_from_filename` tables; `slugify`; `is_in_target_structure`.

#### - [ ] P3-T3 apply
- Owns: `tidyup/apply.py`, `tests/test_apply.py`. Reads: fsops, safety, journal, models, config, winutil, console. Depends: P2-T2.
- Acceptance: §1.3 apply; only approved non-skip rows; journal complete; continues past failures.
- Tests (hand-written manifests over the fake tree): `test_only_approved_rows`; `--limit`, `--only-rule`, `--only-folder`; `test_review_delete_moves_not_deletes` (review mode) and recycle mode calls mocked `winutil.send_to_recycle_bin`; `test_refuses_protected_and_hidden_rows`; `test_refuses_row_inside_atomic_folder` (file in `.git` and `src/main.py`); atomic folder row moved whole; destination collision → " (2)"; `test_locked_file_skipped_and_logged` (patch `fsops.move_file` to raise FileLockedError for one row; others applied; journal has skip); dry run changes nothing and writes no log; confirmation refused → nothing done; `command` with `--yes`.

#### - [ ] P3-T4 undo
- Owns: `tidyup/undo.py`, `tests/test_undo.py`. Reads: fsops, journal, models, config, console. Depends: P2-T2.
- Acceptance: §1.3 undo.
- Tests (journals produced by calling `fsops.move_file`/`move_tree` with a `Journal`): restores files and removes created dirs (snapshot equal); cross-volume undo (`cross_volume` fixture); occupied original path → " (2)" + warning; missing dst skipped; trash entries listed as manual; undo of an interrupted run (no run_end); undo writes its own log and marks original undone; latest-run default; undoing the undo re-applies; `--list` output; dry run changes nothing.

#### - [ ] P3-T5 report
- Owns: `tidyup/report.py`, `tests/test_report.py`. Reads: models, dupes, extract, config, console. Depends: P2-T3, P2-T4.
- Acceptance: §1.3 report; self-contained; all section ids present.
- Tests: build from fixture records (constructed directly) + hand-written rows; sections `summary`, `tree`, `screenshots`, `duplicates`, `near-duplicates`, `versions`, `onedrive` present; screenshot `<img src="data:image/jpeg;base64,`; cloud-only screenshot shows placeholder and `open_spy` has no cloud path; no `http://`/`https://` in src/href; HTML escaping of `<script>` in a filename; thumbnail cap; rows None → "Run tidyup plan"; output parses with `html.parser` without error.

#### - [ ] P3-T6 migrate
- Owns: `tidyup/migrate.py`, `tests/test_migrate.py`. Reads: fsops, safety, journal, models, config, winutil, apply (ApplyResult type), console. Depends: P2-T2.
- Acceptance: §1.3 migrate; never moves without a manifest; resumable.
- Tests (target `tmp_path/"D"`, `cross_volume` fixture): dry run writes migrate manifest, moves nothing; `test_requires_manifest`; `test_refuses_insufficient_space` (mock `winutil.free_space`); `test_verify_before_remove` (spy order) and corrupt copy keeps source; atomic folder migrated whole incl. `.git`; cloud-only rows skipped and never opened; OneDrive folders skipped by default; resume after simulated crash (patch `fsops.move_file` to raise KeyboardInterrupt on the 3rd file) → second run completes with no " (2)" duplicates and every file present once; timestamps preserved; undo of a migrate run restores sources.

### Phase 4 — Planning and upkeep (parallel; depend on Phase 3)

#### - [ ] P4-T1 plan
- Owns: `tidyup/plan.py`, `tests/test_plan.py`. Reads: rules, dupes, config, models, fsops (`unique_path`), scan (tests only), console. Depends: P3-T1, P3-T2.
- Acceptance: §1.3 plan algorithm; every inventory record yields exactly one row; manifest opens with the exact 8 columns.
- Tests (scan the fixture with `mock_attrs`, then plan): one row per record; actions/destinations for every §3 handle; duplicates → `_Duplicates` with "duplicate of" note, keeper not moved; version-chain older rows N with note; `test_collision_reserved` (notes.txt pair → `notes.txt` and `notes (2).txt`; existing review screenshot → " (2)"); `test_onedrive_exit_warning` (dest_root outside OneDrive → WARNING note, N, printed block; cloud-only → `cloud_only_leave_onedrive` skip); in place keeps OneDrive files in OneDrive; atomic rows skip; `already_filed` for files in target structure; approved defaults; dry run writes nothing; missing inventory → friendly error.

#### - [ ] P4-T2 upkeep
- Owns: `tidyup/upkeep.py`, `tests/test_upkeep.py`. Reads: scan, plan, apply, config, models, console. Depends: P3-T1, P3-T3 (plan via its §1.3 interface).
- Acceptance: §1.3 upkeep; never selects review_delete/duplicate or non-high rows. Asks once before moving anything unless `--yes`.
- Tests: `test_only_high_move_rename` — monkeypatch `plan.build_manifest` to return a crafted mix (high move, high rename, medium move, high review_delete, high duplicate, low move_rename) over fixture files; only the first two applied; upkeep manifest + `kind="upkeep"` journal written; upkeep.log line appended; sources limited to downloads/desktop/screenshots (scan called with `with_sources`); dry run changes nothing. `test_confirmation`: `command` with input 'n' moves nothing; non-interactive without `--yes` refused (exit 1, nothing moved); `--yes` proceeds.

### Phase 5 — Integration and docs (parallel; depend on Phase 4)

#### - [ ] P5-T1 End-to-end safety tests
- Owns: `tests/test_e2e.py`. Reads: everything. Depends: Phase 4.
- Acceptance: tests drive the public functions (`scan.scan_sources`, `plan.build_manifest`, `models.write/read_manifest`, `apply.apply_rows`, `undo.undo_run`, `report.build_report`, `upkeep.run_upkeep`, `migrate`) and `cli.main([...,"--yes"])` once. Do not edit modules: report any defect found (file, function, failing assertion).
- Tests: `test_plan_apply_undo_restores_tree_byte_for_byte` (all non-skip rows approved; snapshot of `home` identical after undo, incl. long-path file and collision pair); same with default approvals; `test_atomic_folders_never_split` (after apply each atomic dir's relative snapshot identical, nothing inside moved); `test_cloud_only_never_opened` (`open_spy` + `mock_attrs` through scan, plan, report, apply, upkeep); `test_file_count_preserved` after apply (nothing deleted; review_delete files present in `_Review_Delete`); cross-volume apply + undo with `cross_volume`; migrate to a `D` dir then undo restores; CLI walkthrough scan → plan → apply `--only-folder downloads --limit 50 --yes` → undo.

#### - [ ] P5-T2 README.md
- Owns: `README.md`. Reads: SPEC, PLAN, code. Depends: Phase 4.
- Acceptance: written for a non-programmer: install Python on Windows (tick "Add python.exe to PATH"), download the repo (ZIP and git), create/activate a venv in PowerShell and cmd (incl. `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` note), `pip install -r requirements.txt`; edit config.yaml; first-run walkthrough exactly: scan → plan → review `manifest.csv` in Excel (change approved Y/N, save as CSV) and `report.html` → `apply --only-folder downloads --limit 50` → check results → `undo` test → full `apply`; D: migration (`migrate --to D:\ --dry-run`, review, run, resume, then change folder Location in Windows); what `tidyup upkeep` does and when to run it (on demand, whenever Downloads/Desktop get messy; there is no scheduled task); command reference; **Safety** section listing exactly what the tool will never do (S1–S14 in plain words, incl. Recycle Bin undo limitation); Defaults section (copy of §5); troubleshooting (locked files, OneDrive warnings, long paths).

#### - [ ] P5-T3 CLAUDE.md
- Owns: `CLAUDE.md`. Reads: SPEC, PLAN, code. Depends: Phase 4.
- Acceptance: for future Claude sessions: package/folder structure and module responsibilities, target folder structure, naming conventions (`YYYY-MM-DD_Category_Short-Description.pdf`, " (n)" collisions, rule names), rule summary and confidence table, hard safety rules + where each is enforced (§2), mockability conventions (§0), how to run tests, "never add a delete path outside fsops".
- Tests: none.

---

## 5. Defaults chosen where the spec was silent

1. **In-place layout**: `Documents/<Category>` → the real Documents folder; `Pictures/...`, `Videos/...` → real Pictures/Videos; `Installers`, `Archives` → inside Downloads; `_Unsorted`, `_Duplicates`, `_Review_Delete` → user profile folder (e.g. `C:\Users\me\_Unsorted`). Files already in OneDrive stay in OneDrive (mirrored structure under the OneDrive root).
2. **Work files** (`inventory.csv`, `manifest.csv`, `report.html`, `logs/`) in `tidyup_data\` next to `config.yaml`; always excluded from scans.
3. **Atomic folders** are inventoried as one row (no inner files) and left in place when organizing in place; with a destination root they move whole (medium confidence, approved N). Source roots themselves are never atomic. Extra markers: `.venv`, `pyvenv.cfg`; extra app folders listed in the schema.
4. **Confidence**: photos EXIF or filename date → high, modified date → medium; videos filename date → high, else medium; installers/archives in Downloads → high; old installers → review_delete low; exact duplicates → high (approved Y); documents moved (never renamed) — docx with strong text match high, filename match medium; screenshots high at score ≥ 5, medium at 3–4.
5. **Scanned PDFs** with a filename keyword go to `Documents/<Category>` with their original name (medium); otherwise `_Unsorted/PDFs`.
6. **Unsorted** is split by type group: `_Unsorted/{Images,Videos,Audio,PDFs,Documents,Installers,Archives,Other}`.
7. **Files already in the target structure** are left alone unless a high-confidence rule applies (`already_filed`).
8. **Version chains**: only older members are forced approved=N; the newest keeps its normal default.
9. **Exact-duplicate keeper**: location priority Documents > Pictures > Videos > OneDrive > Desktop > Downloads, then clean name, then oldest created, then shortest path. Empty files are never treated as duplicates.
10. **Near-duplicates**: perceptual hash distance ≤ 6; report only.
11. **Dates in PDFs** default to US order (MM/DD/YYYY); set `rules.pdf.date_order: DMY` to change.
12. **Long-path prefix** applied when a path is ≥ 240 characters.
13. **CSV**: UTF-8 with BOM, comma delimiter (configurable); reader accepts Excel re-saves (semicolon, cp1252, yes/x/1).
14. **Moves out of OneDrive** are always approved=N with a WARNING note; cloud-only files are never moved out of OneDrive or across drives (that would download them).
15. **Recycle Bin mode**: allowed, but `tidyup undo` cannot restore those items; it lists them for manual restore. Default is the review folder.
16. **Migrate**: default folders Documents, Pictures, Videos, Downloads plus `_Unsorted`, `_Duplicates`, `_Review_Delete`; target `D:\<FolderName>`; OneDrive folders skipped; 1 GiB free-space margin; a dry run must create `migrate_manifest.csv` first; empty source folders are left behind; Windows known-folder locations are not changed automatically.
17. **No scheduled task** (user request): tidyup only runs when the user starts it. `tidyup upkeep` is the on-demand quick tidy; it asks once before moving anything unless `--yes`.
18. **Extra commands/options** beyond the spec: `tidyup upkeep` (on-demand quick tidy), `apply --only-folder NAME` (for the "Downloads only" walkthrough step), `apply/migrate/upkeep --yes`, `undo --list`, `report --open`, global `--debug`, `--version`.
19. **`--dry-run`**: scan/plan/report compute and print but write nothing; apply/undo/upkeep print planned operations; migrate's dry run writes only `migrate_manifest.csv`.
20. **Exit codes**: 0 ok, 1 error/refused, 2 bad arguments, 130 interrupted. Interactive confirmation before apply/migrate unless `--yes`; non-interactive without `--yes` is refused.
21. **Symlinks and junctions** are never followed or moved; created timestamps on non-Windows use birth time if available, else min(ctime, mtime).
