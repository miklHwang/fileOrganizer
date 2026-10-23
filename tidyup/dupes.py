"""Exact duplicates, keeper choice, version chains and near-duplicates (pure functions over records)."""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tidyup import winutil
from tidyup.config import Config
from tidyup.models import EntryType, FileRecord


@dataclass
class DuplicateGroup:
    sha256: str
    keeper: FileRecord
    duplicates: list[FileRecord]


@dataclass
class VersionChain:
    key: str
    current: FileRecord
    older: list[FileRecord]


@dataclass
class NearDuplicateGroup:
    members: list[FileRecord]
    max_distance: int


def _path_key(rec: FileRecord) -> str:
    return str(rec.path).casefold()


def _version_patterns(cfg: Config) -> list[str]:
    return list((cfg.rules.get("version_chains") or {}).get("patterns") or [])


def exact_duplicate_groups(records: Sequence[FileRecord], cfg: Config) -> list[DuplicateGroup]:
    min_size = int((cfg.rules.get("duplicates") or {}).get("min_size", 1))
    by_hash: dict[str, list[FileRecord]] = defaultdict(list)
    for r in records:
        if (r.entry_type == EntryType.FILE and not r.is_cloud_only and r.sha256
                and r.size >= min_size and r.size > 0):
            by_hash[r.sha256].append(r)
    groups: list[DuplicateGroup] = []
    for sha, members in by_hash.items():
        if len(members) < 2:
            continue
        keeper = choose_keeper(members, cfg)
        others = sorted((m for m in members if m is not keeper), key=_path_key)
        groups.append(DuplicateGroup(sha256=sha, keeper=keeper, duplicates=others))
    groups.sort(key=lambda g: (_path_key(g.keeper), g.sha256))
    return groups


def choose_keeper(records: Sequence[FileRecord], cfg: Config) -> FileRecord:
    priority = [str(p).casefold() for p in
                ((cfg.rules.get("duplicates") or {}).get("location_priority") or [])]
    compiled = [re.compile(p, re.IGNORECASE) for p in _version_patterns(cfg)]

    def key(r: FileRecord):
        loc = cfg.folder_containing(r.path)
        rank = priority.index(loc.casefold()) if loc and loc.casefold() in priority else len(priority)
        versiony = 1 if any(p.search(r.path.stem) for p in compiled) else 0
        return (rank, versiony, r.created, len(str(r.path)), _path_key(r))

    return min(records, key=key)


def normalize_version_stem(stem: str, patterns: Sequence[str]) -> str:
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    s = stem.casefold()
    while True:
        before = s
        for p in compiled:
            s = p.sub("", s)
        if s == before:
            break
    collapsed = re.sub(r"[\s_-]+", " ", s).strip()
    if not collapsed:  # the whole name was a marker word (e.g. "draft"): keep it
        collapsed = re.sub(r"[\s_-]+", " ", stem.casefold()).strip()
    return collapsed


def version_chains(records: Sequence[FileRecord], cfg: Config,
                   exclude: set[Path] = frozenset()) -> list[VersionChain]:
    patterns = _version_patterns(cfg)
    excluded = {winutil.norm_key(p) for p in exclude}
    groups: dict[str, list[tuple[FileRecord, bool]]] = defaultdict(list)
    for r in records:
        if r.entry_type != EntryType.FILE or winutil.norm_key(r.path) in excluded:
            continue
        norm = normalize_version_stem(r.path.stem, patterns)
        differs = norm != re.sub(r"[\s_-]+", " ", r.path.stem.casefold()).strip()
        key = f"{winutil.norm_key(r.path.parent)}|{norm}|{r.path.suffix.casefold()}"
        groups[key].append((r, differs))
    chains: list[VersionChain] = []
    for key, items in groups.items():
        if len(items) < 2 or not any(d for _, d in items):
            continue
        members = [r for r, _ in items]
        current = max(members, key=lambda r: (r.modified, _path_key(r)))
        older = sorted((m for m in members if m is not current), key=_path_key)
        chains.append(VersionChain(key=key, current=current, older=older))
    chains.sort(key=lambda c: (_path_key(c.current), c.key))
    return chains


# ----------------------------------------------------------------------------- near duplicates

def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def near_duplicate_groups(records: Sequence[FileRecord], cfg: Config) -> list[NearDuplicateGroup]:
    opts = cfg.rules.get("near_duplicates") or {}
    if not opts.get("enabled", True):
        return []
    max_dist = int(opts.get("max_distance", 6))

    by_hash: dict[int, list[FileRecord]] = defaultdict(list)
    for r in records:
        if r.entry_type != EntryType.FILE or not r.phash:
            continue
        try:
            by_hash[int(r.phash, 16)].append(r)
        except ValueError:
            continue
    values = sorted(by_hash)
    if not values:
        return []

    parent = {v: v for v in values}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # Pigeonhole multi-index: split the 64 bits into max_dist+1 chunks; any pair within max_dist
    # differs in at most max_dist chunks, so it must match exactly in at least one chunk.
    nchunks = min(64, max_dist + 1)
    bounds = [(64 * i) // nchunks for i in range(nchunks + 1)]
    for i in range(nchunks):
        lo, width = bounds[i], bounds[i + 1] - bounds[i]
        mask = (1 << width) - 1
        buckets: dict[int, list[int]] = defaultdict(list)
        for v in values:
            buckets[(v >> lo) & mask].append(v)
        for bucket in buckets.values():
            for j in range(1, len(bucket)):
                v = bucket[j]
                for k in range(j):
                    w = bucket[k]
                    if (v ^ w).bit_count() <= max_dist:
                        union(v, w)

    clusters: dict[int, list[int]] = defaultdict(list)
    for v in values:
        clusters[find(v)].append(v)

    groups: list[NearDuplicateGroup] = []
    for hashes in clusters.values():
        members = [r for h in hashes for r in by_hash[h]]
        if len(members) < 2:
            continue
        shas = {m.sha256 for m in members}
        if len(shas) == 1 and None not in shas:
            continue
        far = 0
        for i, a in enumerate(hashes):
            for b in hashes[i + 1:]:
                far = max(far, _hamming(a, b))
        members.sort(key=_path_key)
        groups.append(NearDuplicateGroup(members=members, max_distance=far))
    groups.sort(key=lambda g: _path_key(g.members[0]))
    return groups
