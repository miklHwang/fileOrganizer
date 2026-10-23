from __future__ import annotations

from pathlib import Path

import pytest

from tidyup import config, console, winutil
from tidyup.errors import ConfigError

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    d = {"home": h}
    for n in ("documents", "desktop", "downloads", "pictures", "videos"):
        (h / n.capitalize()).mkdir(parents=True)
        d[n] = h / n.capitalize()
    d["onedrive"] = None
    monkeypatch.setattr(winutil, "known_folders", lambda: dict(d))
    return d


def test_defaults_load(home):
    cfg = config.load_config()
    assert cfg.path is None and cfg.config_dir == Path.cwd()
    assert cfg.folders["screenshots"] == home["pictures"] / "Screenshots"
    assert [s.label for s in cfg.sources] == ["documents", "desktop", "downloads", "pictures", "videos"]
    assert cfg.delete_mode == "review" and cfg.dest_root is None
    assert list(cfg.categories)[0] == "Finance" and "1099" in cfg.categories["Taxes"]["keywords"]
    assert cfg.inventory_path == cfg.work_dir / "inventory.csv"
    assert cfg.logs_dir == cfg.work_dir / "logs"


def test_file_and_overrides_merge(home, tmp_path):
    f = tmp_path / "cfg" / "config.yaml"
    f.parent.mkdir()
    f.write_text("delete_mode: recycle_bin\nnaming:\n  description_max_len: 20\nsources: [downloads]\n")
    cfg = config.load_config(f, overrides={"naming": {"date_format": "%d"}})
    assert cfg.delete_mode == "recycle_bin"
    assert cfg.naming["description_max_len"] == 20 and cfg.naming["date_format"] == "%d"
    assert cfg.naming["pdf_pattern"] == "{date}_{category}_{description}"
    assert [s.label for s in cfg.sources] == ["downloads"]
    assert cfg.work_dir == f.parent.resolve() / "tidyup_data"


def test_errors(home, tmp_path):
    with pytest.raises(ConfigError, match="bogus"):
        config.load_config(None, {"bogus": 1})
    with pytest.raises(ConfigError, match="delete_mode"):
        config.load_config(None, {"delete_mode": "burn"})
    with pytest.raises(ConfigError, match="version"):
        config.load_config(None, {"version": 2})
    with pytest.raises(ConfigError, match="structure.photos"):
        config.load_config(None, {"structure": {"photos": "Elsewhere/{yyyy}"}})
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nfolders:\n  home: [unclosed\nsources: x\n")
    with pytest.raises(ConfigError, match="line"):
        config.load_config(bad)


def test_work_dir_absolute_kept(home, tmp_path):
    cfg = config.load_config(None, {"work_dir": str(tmp_path / "w")})
    assert cfg.work_dir == tmp_path / "w"


def test_kfm_dedup(home, tmp_path, monkeypatch):
    od = tmp_path / "OneDrive"
    (od / "Documents").mkdir(parents=True)
    home["onedrive"] = od
    home["documents"] = od / "Documents"
    infos = []
    monkeypatch.setattr(console, "info", infos.append)
    cfg = config.load_config()
    labels = [s.label for s in cfg.sources]
    assert labels.count("onedrive") == 1 and "documents" not in labels
    assert all(s.in_onedrive for s in cfg.sources if s.label == "onedrive")
    assert any("documents" in m for m in infos)


def test_missing_source_dropped_with_warning(home, monkeypatch):
    warns = []
    monkeypatch.setattr(console, "warn", warns.append)
    cfg = config.load_config()
    assert "onedrive" not in [s.label for s in cfg.sources]
    assert len([w for w in warns if "onedrive" in w]) == 1


def test_literal_source(home, tmp_path):
    extra = tmp_path / "Stuff"
    extra.mkdir()
    cfg = config.load_config(None, {"sources": [str(extra)]})
    assert cfg.sources[0].label == "Stuff" and cfg.sources[0].path == extra


def test_resolve_destination_in_place(home):
    cfg = config.load_config()
    src = home["downloads"] / "a.pdf"
    assert cfg.resolve_destination("Documents/Finance", src) == home["documents"] / "Finance"
    assert cfg.resolve_destination("pictures/Photos/2024/2024-07", src) == home["pictures"] / "Photos/2024/2024-07"
    assert cfg.resolve_destination("Installers", src) == home["downloads"] / "Installers"
    assert cfg.resolve_destination("_Unsorted/PDFs", src) == home["home"] / "_Unsorted/PDFs"


def test_resolve_destination_dest_root_and_fallback(home, tmp_path):
    cfg = config.load_config(None, {"destination": {"root": str(tmp_path / "D")}})
    assert cfg.resolve_destination("Documents/Finance", home["downloads"] / "a") == tmp_path / "D/Documents/Finance"
    home["videos"] = None
    cfg = config.load_config()
    assert cfg.resolve_destination("Videos/2024", home["downloads"] / "a") == home["home"] / "Videos/2024"


def test_resolve_destination_onedrive_guard(home, tmp_path):
    od = tmp_path / "OneDrive"
    (od / "Stuff").mkdir(parents=True)
    home["onedrive"] = od
    cfg = config.load_config()
    inside = od / "Stuff" / "a.pdf"
    assert cfg.resolve_destination("Documents/Finance", inside) == od / "Documents/Finance"
    assert cfg.resolve_destination("_Unsorted/PDFs", inside) == od / "_Unsorted/PDFs"
    outside = home["downloads"] / "a.pdf"
    assert cfg.resolve_destination("Documents/Finance", outside) == home["documents"] / "Finance"


def test_folder_containing_most_specific(home):
    cfg = config.load_config()
    shots = home["pictures"] / "Screenshots" / "x.png"
    assert cfg.folder_containing(shots) == "screenshots"
    assert cfg.folder_containing(home["pictures"] / "y.jpg") == "pictures"
    assert cfg.folder_containing(home["home"] / "other" / "z") == "home"
    assert cfg.folder_containing(home["home"].parent / "elsewhere") is None


def test_with_sources(home):
    cfg = config.load_config()
    sub = cfg.with_sources(["downloads", "screenshots"])
    assert [s.label for s in sub.sources] == ["downloads"]  # Screenshots dir missing -> dropped
    (home["pictures"] / "Screenshots").mkdir()
    sub = cfg.with_sources(["downloads", "screenshots"])
    assert [s.label for s in sub.sources] == ["downloads", "screenshots"]
    assert len(cfg.sources) == 5


def test_find_default_config(tmp_path):
    assert config.find_default_config(tmp_path) is None
    (tmp_path / "config.yaml").write_text("version: 1\n")
    assert config.find_default_config(tmp_path) == tmp_path / "config.yaml"


def test_deep_merge():
    assert config.deep_merge({"a": {"b": 1, "c": [1]}}, {"a": {"c": [2]}, "d": 3}) == {"a": {"b": 1, "c": [2]}, "d": 3}


def test_repo_config_identical_to_packaged_default():
    assert (REPO / "config.yaml").read_bytes() == (REPO / "tidyup" / "default_config.yaml").read_bytes()
    assert config.default_config_text() == (REPO / "config.yaml").read_text("utf-8")


def test_ensure_app_config_creates_once(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    p = config.ensure_app_config()
    assert p == tmp_path / "tidyup" / "config.yaml"
    assert p.read_text("utf-8") == config.default_config_text()
    p.write_text("version: 1\n# edited\n", "utf-8")
    assert config.ensure_app_config() == p
    assert p.read_text("utf-8") == "version: 1\n# edited\n"


def test_find_default_config_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert config.is_frozen() is False and config.app_install_dir() is None
    assert config.find_default_config(tmp_path) is None
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(tmp_path / "x" / "tidyup.exe"))
    assert config.is_frozen() is True
    assert config.app_install_dir() == (tmp_path / "x").resolve()
    assert config.find_default_config(tmp_path) == tmp_path / "tidyup" / "config.yaml"
    assert (tmp_path / "tidyup" / "config.yaml").is_file()


def test_frozen_app_dir_excluded_from_scan(fake_tree, mock_attrs, monkeypatch):
    from tidyup import scan

    base = config.load_config(None, overrides=fake_tree.config_overrides())
    app = base.folders["downloads"] / "tidyup"
    (app / "_internal").mkdir(parents=True)
    (app / "tidyup.exe").write_bytes(b"MZ")
    (app / "_internal" / "x.dll").write_bytes(b"dll")
    with monkeypatch.context() as m:
        m.setattr("sys.frozen", True, raising=False)
        m.setattr("sys.executable", str(app / "tidyup.exe"))
        cfg = config.load_config(None, overrides=fake_tree.config_overrides())
        assert app.resolve() in cfg.exclusions["paths"]
        res = scan.scan_sources(cfg, show_progress=False)
        assert res.records
        assert not [r for r in res.records if winutil.is_within(r.path, app)]
    res2 = scan.scan_sources(config.load_config(None, overrides=fake_tree.config_overrides()), show_progress=False)
    assert [r for r in res2.records if winutil.is_within(r.path, app)]


def test_ensure_config_file_never_overwrites(tmp_path):
    p = tmp_path / "sub" / "config.yaml"
    assert config.ensure_config_file(p) == p
    assert p.read_text(encoding="utf-8") == config.default_config_text()
    p.write_text("# mine\n", encoding="utf-8")
    config.ensure_config_file(p)
    assert p.read_text(encoding="utf-8") == "# mine\n"
