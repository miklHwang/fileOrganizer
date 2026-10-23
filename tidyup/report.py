"""tidyup report: a single self-contained report.html (no external URLs, no JavaScript)."""
from __future__ import annotations

import html
import webbrowser
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from tidyup import console, dupes, extract, models, winutil
from tidyup.errors import TidyupError
from tidyup.models import Action, EntryType, FileRecord, ManifestRow

if TYPE_CHECKING:
    import argparse

    from tidyup.config import Config

_NEED_PLAN = "Run tidyup plan to see proposed changes."
_PLACEHOLDER = "cloud-only — not downloaded"
_OUT_OF_ONEDRIVE = "WARNING: moves this file OUT of OneDrive"

_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;background:#fafafa}
h1{margin-top:0}h2{border-bottom:2px solid #ccc;padding-bottom:4px;margin-top:32px}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 10px;text-align:left}
.grid{display:flex;flex-wrap:wrap;gap:12px}
.card{border:1px solid #bbb;background:#fff;border-radius:6px;padding:8px;width:200px;overflow-wrap:anywhere;font-size:12px}
.card.keep{border-color:#2a8a2a;border-width:2px}
.keepmark{background:#2a8a2a;color:#fff;font-weight:bold;padding:1px 6px;border-radius:3px}
.ph{background:#ccc;color:#555;display:flex;align-items:center;justify-content:center;min-height:90px;text-align:center}
.group{display:flex;flex-wrap:wrap;gap:12px;margin:10px 0;padding:8px;border:1px dashed #aaa;align-items:flex-start}
.warn{background:#fff0d0;border:1px solid #d9a400;padding:6px;margin:6px 0}
details{margin-left:18px}summary{cursor:pointer}.muted{color:#777}
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


class _Thumbs:
    """Thumbnail budget shared across the whole report."""

    def __init__(self, cfg: Config) -> None:
        opts = cfg.report or {}
        self.remaining = int(opts.get("max_thumbnails", 400))
        self.px = int(opts.get("thumbnail_px", 160))

    def card_image(self, rec: FileRecord | None, *, force: bool = False) -> str:
        """HTML for a thumbnail slot ('' when none should be shown). Never touches cloud-only files."""
        if rec is None:
            return ""
        if rec.is_cloud_only:
            return f'<div class="ph">{_e(_PLACEHOLDER)}</div>'
        if not force and models.type_group(rec.extension) != "Images":
            return ""
        if self.remaining <= 0:
            return ""
        uri = extract.thumbnail_data_uri(rec.path, is_cloud_only=False, max_px=self.px)
        if not uri:
            return ""
        self.remaining -= 1
        return f'<img src="{_e(uri)}" alt="" style="max-width:{self.px}px;max-height:{self.px}px">'


def _card(rec: FileRecord, thumbs: _Thumbs, *, keep: bool = False, extra: str = "") -> str:
    mark = '<span class="keepmark">KEEP</span><br>' if keep else ""
    return (f'<div class="card{" keep" if keep else ""}">{mark}{thumbs.card_image(rec)}'
            f'<div><b>{_e(rec.path.name)}</b></div><div class="muted">{_e(rec.path.parent)}</div>'
            f'<div>{_e(rec.size)} bytes</div>{extra}</div>')


def _summary(cfg: Config, records: Sequence[FileRecord], rows: Sequence[ManifestRow] | None) -> str:
    files = [r for r in records if r.entry_type == EntryType.FILE]
    atomic = [r for r in records if r.entry_type == EntryType.ATOMIC_FOLDER]
    total = sum(r.size for r in records)
    items = [("Files", len(files)), ("Total size", console.human_size(total)),
             ("Cloud-only files", sum(1 for r in records if r.is_cloud_only)),
             ("Atomic folders", len(atomic))]
    out = ["<table>"] + [f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in items] + ["</table>"]
    if rows is None:
        out.append(f"<p>{_e(_NEED_PLAN)}</p>")
    else:
        counts = Counter((str(r.action.value if isinstance(r.action, Action) else r.action),
                          str(r.confidence.value if hasattr(r.confidence, "value") else r.confidence))
                         for r in rows)
        out.append("<h3>Proposed changes by action and confidence</h3><table>"
                   "<tr><th>Action</th><th>Confidence</th><th>Rows</th></tr>")
        for (a, c), n in sorted(counts.items()):
            out.append(f"<tr><td>{_e(a)}</td><td>{_e(c)}</td><td>{n}</td></tr>")
        out.append("</table>")
        out.append(f"<p>Approved rows: <b>{sum(1 for r in rows if r.approved)}</b> of {len(rows)}</p>")
        n_od = sum(1 for r in rows if _OUT_OF_ONEDRIVE in r.notes)
        if n_od:
            out.append(f'<div class="warn">{n_od} proposed move(s) would take files OUT of OneDrive. '
                       f'See the OneDrive section.</div>')
    return "".join(out)


def _tree(rows: Sequence[ManifestRow] | None) -> str:
    if rows is None:
        return f"<p>{_e(_NEED_PLAN)}</p>"
    root: dict = {"n": 0, "kids": {}}
    for r in rows:
        if r.action == Action.SKIP or r.destination is None:
            continue
        parts = [p for p in Path(winutil.strip_long_path(r.destination)).parent.parts if p not in ("\\", "/")]
        node = root
        node["n"] += 1
        for p in parts:
            node = node["kids"].setdefault(p, {"n": 0, "kids": {}})
            node["n"] += 1
    if not root["n"]:
        return "<p>No files are proposed to move.</p>"

    def render(name: str, node: dict, depth: int) -> str:
        label = f"{_e(name)} ({node['n']})"
        if not node["kids"]:
            return f"<div style='margin-left:18px'>{label}</div>"
        inner = "".join(render(k, v, depth + 1) for k, v in sorted(node["kids"].items(), key=lambda kv: kv[0].casefold()))
        return f"<details{' open' if depth < 2 else ''}><summary>{label}</summary>{inner}</details>"

    return "".join(render(k, v, 0) for k, v in sorted(root["kids"].items(), key=lambda kv: kv[0].casefold()))


def _screenshots(records: Sequence[FileRecord], rows: Sequence[ManifestRow] | None, thumbs: _Thumbs) -> str:
    if rows is None:
        return f"<p>{_e(_NEED_PLAN)}</p>"
    by_path = {winutil.norm_key(r.path): r for r in records}
    shots = [r for r in rows if r.rule == "screenshot"]
    if not shots:
        return "<p>No screenshots detected.</p>"
    out = ['<div class="grid">']
    for row in shots:
        rec = by_path.get(winutil.norm_key(row.source))
        if rec is None:
            img = f'<div class="ph">{_e(_PLACEHOLDER)}</div>' if "cloud-only" in row.notes else ""
        else:
            img = thumbs.card_image(rec, force=True)
        out.append(f'<div class="card">{img}<div><b>{_e(row.source.name)}</b></div>'
                   f'<div>Confidence: {_e(row.confidence.value)}</div><div>{_e(row.notes)}</div></div>')
    out.append("</div>")
    return "".join(out)


def _duplicates(cfg: Config, records: Sequence[FileRecord], thumbs: _Thumbs) -> str:
    groups = dupes.exact_duplicate_groups(records, cfg)
    if not groups:
        return "<p>No exact duplicates found.</p>"
    out = []
    for i, g in enumerate(groups, 1):
        out.append(f'<div class="group"><div><b>D{i}</b><br><span class="muted">sha256 {_e(g.sha256[:12])}</span></div>')
        out.append(_card(g.keeper, thumbs, keep=True))
        out.extend(_card(d, thumbs) for d in g.duplicates)
        out.append("</div>")
    return "".join(out)


def _near(cfg: Config, records: Sequence[FileRecord], thumbs: _Thumbs) -> str:
    groups = dupes.near_duplicate_groups(records, cfg)
    if not groups:
        return "<p>No near-duplicate photos found.</p>"
    out = ["<p>Report only: these are never moved automatically.</p>"]
    for i, g in enumerate(groups, 1):
        out.append(f'<div class="group"><div><b>G{i}</b><br><span class="muted">distance {g.max_distance}</span></div>')
        out.extend(_card(m, thumbs) for m in g.members)
        out.append("</div>")
    return "".join(out)


def _versions(cfg: Config, records: Sequence[FileRecord], thumbs: _Thumbs) -> str:
    excluded = {d.path for g in dupes.exact_duplicate_groups(records, cfg) for d in g.duplicates}
    chains = dupes.version_chains(records, cfg, exclude=excluded)
    if not chains:
        return "<p>No likely old versions found.</p>"
    out = ["<h3>Likely old versions</h3>"]
    for c in chains:
        out.append('<div class="group">')
        out.append(_card(c.current, thumbs, keep=True, extra="<div>current version</div>"))
        out.extend(_card(o, thumbs, extra="<div>likely old version</div>") for o in c.older)
        out.append("</div>")
    return "".join(out)


def _onedrive(rows: Sequence[ManifestRow] | None) -> str:
    if rows is None:
        return f"<p>{_e(_NEED_PLAN)}</p>"
    hits = [r for r in rows if "WARNING" in r.notes and "OneDrive" in r.notes]
    if not hits:
        return "<p>No proposed move takes files out of OneDrive.</p>"
    out = ['<div class="warn">These moves would remove files from OneDrive (they will no longer be in the cloud). '
           'They are not approved by default.</div><table><tr><th>Source</th><th>Destination</th><th>Notes</th></tr>']
    for r in hits:
        out.append(f"<tr><td>{_e(r.source)}</td><td>{_e(r.destination or '')}</td><td>{_e(r.notes)}</td></tr>")
    out.append("</table>")
    return "".join(out)


def build_report(cfg: Config, records: Sequence[FileRecord], rows: Sequence[ManifestRow] | None) -> str:
    thumbs = _Thumbs(cfg)
    # Screenshots first: they are the main thing the user wants to eyeball.
    shots = _screenshots(records, rows, thumbs)
    sections = [
        ("summary", "Summary", _summary(cfg, records, rows)),
        ("tree", "Proposed folder tree", _tree(rows)),
        ("screenshots", "Detected screenshots", shots),
        ("duplicates", "Exact duplicates", _duplicates(cfg, records, thumbs)),
        ("near-duplicates", "Near-duplicate photos", _near(cfg, records, thumbs)),
        ("versions", "Likely old versions", _versions(cfg, records, thumbs)),
        ("onedrive", "OneDrive warnings", _onedrive(rows)),
    ]
    body = "".join(f'<section id="{i}"><h2>{_e(t)}</h2>{c}</section>' for i, t, c in sections)
    return ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>tidyup report</title>'
            f"<style>{_CSS}</style></head><body><h1>tidyup report</h1>{body}</body></html>\n")


def command(cfg: Config, args: argparse.Namespace) -> int:
    if not winutil_exists(cfg.inventory_path):
        raise TidyupError("No inventory found. Run 'tidyup scan' first.")
    records = models.read_inventory(cfg.inventory_path)
    rows = models.read_manifest(cfg.manifest_path) if winutil_exists(cfg.manifest_path) else None
    if rows is None:
        console.warn("No manifest yet; the report will not show proposed changes. Run 'tidyup plan' first.")
    text = build_report(cfg, records, rows)
    if getattr(args, "dry_run", False):
        console.info(f"Dry run: report ({len(text)} characters) not written.")
        return 0
    path = cfg.report_path
    Path(winutil.long_path(path.parent)).mkdir(parents=True, exist_ok=True)
    with open(winutil.long_path(path), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    console.success(f"Report written to {path}")
    if getattr(args, "open", False):
        webbrowser.open(path.as_uri())
    return 0


def winutil_exists(path: Path) -> bool:
    return Path(winutil.long_path(path)).exists()
