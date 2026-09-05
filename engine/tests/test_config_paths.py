"""Where the app keeps the user's data, and how one setting moves all of it.

``data.dir`` is the root; the DB, recordings, models and runtime packs default
to subpaths of it, and each can be pointed elsewhere on its own. The rule the
tests hold: an empty key means "under the root", an explicit key wins, and the
resolution happens once at load time so nothing downstream re-derives it.
"""
from __future__ import annotations

from pathlib import Path

from wrenote.core.config import Config, load_config


def _cfg(**data) -> Config:
    return Config.model_validate(data)


def test_everything_defaults_under_the_data_dir():
    cfg = _cfg(data={"dir": "/srv/wrenote"})
    assert cfg.data.db_path == "/srv/wrenote/data.db"
    assert cfg.data.recordings_dir == "/srv/wrenote/recordings"
    assert cfg.models.dir == "/srv/wrenote/models"
    assert cfg.compute.runtimes_dir == "/srv/wrenote/runtimes"


def test_the_default_root_is_the_home_dotdir():
    cfg = _cfg()
    home = Path("~").expanduser()
    assert Path(cfg.data.dir) == home / ".wrenote"
    assert Path(cfg.models.dir) == home / ".wrenote" / "models"


def test_an_explicit_path_wins_over_the_root():
    """A pre-existing ~/.wrenote/config.yaml that pins models.dir keeps working,
    and a user can put just the gigabytes on another drive."""
    cfg = _cfg(data={"dir": "/small/ssd"}, models={"dir": "/big/disk/models"},
               compute={"runtimes_dir": "~/packs"})
    assert cfg.models.dir == "/big/disk/models"
    assert cfg.compute.runtimes_dir == str(Path("~/packs").expanduser())
    assert cfg.data.db_path == "/small/ssd/data.db"


def test_tilde_is_expanded_everywhere():
    cfg = _cfg(data={"dir": "~/elsewhere", "recordings_dir": "~/rec"})
    assert "~" not in cfg.data.dir and "~" not in cfg.data.recordings_dir
    assert cfg.data.recordings_dir == str(Path("~/rec").expanduser())


def test_the_bundled_config_leaves_the_root_in_charge(tmp_path, monkeypatch):
    """engine/config.yaml must not pin models.dir / runtimes_dir to ~/.wrenote,
    or a user's data.dir would move the DB and leave the models behind."""
    from wrenote.core import config as config_mod

    user = tmp_path / "config.yaml"
    user.write_text("data:\n  dir: /moved\n", encoding="utf-8")
    cfg = load_config([config_mod.REPO_DEFAULT_CONFIG, user], use_env=False)
    assert cfg.paths() == {
        "data_dir": "/moved",
        "db_path": "/moved/data.db",
        "recordings_dir": "/moved/recordings",
        "models_dir": "/moved/models",
        "runtimes_dir": "/moved/runtimes",
        "user_config": str(config_mod.user_config_path()),
    }


def test_env_var_moves_the_root(monkeypatch):
    monkeypatch.setenv("WRENOTE_DATA__DIR", "/from/env")
    cfg = load_config([], use_env=True)
    assert cfg.data.db_path == "/from/env/data.db"
    assert cfg.models.dir == "/from/env/models"


def test_recordings_are_served_from_the_configured_dir(client):
    """The one end-to-end link: a WAV under data.recordings_dir is what the
    recording route finds, and deleting the session removes it."""
    rec_dir = Path(client.app.state.config.data.recordings_dir)
    rec_dir.mkdir(parents=True, exist_ok=True)
    (rec_dir / "s1.wav").write_bytes(b"RIFF")
    assert client.get("/v1/recordings/s1.wav").status_code == 200
    assert client.delete("/v1/sessions/s1").status_code == 200
    assert not (rec_dir / "s1.wav").exists()
