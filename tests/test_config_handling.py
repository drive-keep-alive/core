"""config_handling: defaults, merging, load/reload fallbacks."""

import config_handling


def test_defaults_shape():
    cfg = config_handling.DEFAULTS
    assert set(cfg["scheduler"]) == {
        "keep_alive_minutes", "smart_poll_minutes",
        "short_test_days", "long_test_days", "badblocks_days",
    }
    assert cfg["health"]["temp_warn_c"] < cfg["health"]["temp_critical_c"]
    assert cfg["dashboard"]["refresh_seconds"] > 0


def test_deep_merge_overlays_scalars():
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    overlay = {"a": 2, "nested": {"y": 3}}
    out = config_handling._deep_merge(base, overlay)
    assert out["a"] == 2
    assert out["nested"] == {"x": 1, "y": 3}


def test_deep_merge_does_not_mutate_base():
    base = {"nested": {"x": 1}}
    config_handling._deep_merge(base, {"nested": {"y": 2}})
    assert base == {"nested": {"x": 1}}


def test_deep_merge_adds_new_keys():
    out = config_handling._deep_merge({"a": 1}, {"b": {"c": 2}})
    assert out == {"a": 1, "b": {"c": 2}}


def test_load_missing_file_returns_defaults(cfg_path):
    assert config_handling.load() == config_handling.DEFAULTS


def test_load_reads_file_and_merges(cfg_path):
    cfg_path.write_text("health = { temp_warn_c = 45 }\n")
    cfg = config_handling.load()
    assert cfg["health"]["temp_warn_c"] == 45
    assert cfg["health"]["temp_critical_c"] == config_handling.DEFAULTS["health"]["temp_critical_c"]


def test_load_corrupt_file_falls_back(cfg_path):
    cfg_path.write_text("this is [ not toml")
    assert config_handling.load() == config_handling.DEFAULTS


def test_get_loads_on_first_use(cfg_path, monkeypatch):
    monkeypatch.setattr(config_handling, "_config", None)
    assert config_handling.get() == config_handling.DEFAULTS


def test_reload_picks_up_changes(cfg_path):
    config_handling.load()
    cfg_path.write_text("scheduler = { keep_alive_minutes = 3 }\n")
    cfg = config_handling.reload()
    assert cfg["scheduler"]["keep_alive_minutes"] == 3
