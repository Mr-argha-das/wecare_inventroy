"""Isolated test environment: temp DATA_DIR + Feather DB."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.config as config  # noqa: E402


@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    import app.database as db
    monkeypatch.setattr(db, "DATA_DIR", data_dir)
    # re-create per-table locks state is fine; ensure tables
    db.ensure_all()
    return data_dir


@pytest.fixture()
def admin_user(tmp_data):
    from app import database as db
    from app.main import init_defaults
    init_defaults()
    users = db.find_records("staff", username="admin")
    assert not users.empty
    return users.iloc[0].to_dict()
