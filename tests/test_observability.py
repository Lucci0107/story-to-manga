"""合成データだけを使い、観測APIの拒否境界と無変更性を検証する。"""

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability import FIELDS, connect_read_only, create_observer_app


@pytest.fixture
def observer(tmp_path, monkeypatch):
    path = tmp_path / "observer.sqlite3"
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(path)) as db:
        for table, fields in FIELDS.items():
            db.execute(
                f"CREATE TABLE {table} ({','.join(f'{c} TEXT' for c in fields)}, private_text TEXT)"
            )
            for index in range(28):
                values = {c: now for c in fields}
                values.update(
                    id=str(uuid4()),
                    project_id=str(uuid4()),
                    status="failed",
                    current_step="generate",
                    job_type="character",
                    format="pdf",
                )
                db.execute(
                    f"INSERT INTO {table} VALUES ({','.join('?' for _ in range(len(fields) + 1))})",
                    [values[c] for c in fields] + ["PRIVATE-CONTENT-SENTINEL"],
                )
        db.execute("CREATE TABLE sessions (token TEXT)")
        db.execute("INSERT INTO sessions VALUES ('expired-session-must-remain')")
        db.commit()
    monkeypatch.setenv("MCP_OBSERVER_TOKEN", "synthetic-observer-token-for-tests-only")
    client = TestClient(create_observer_app(lambda: path))
    client.headers["Authorization"] = "Bearer synthetic-observer-token-for-tests-only"
    return client, path


def test_observer_auth_and_get_only(observer, monkeypatch):
    client, _ = observer
    assert client.get("/health").status_code == 200
    assert client.get("/health", headers={"Authorization": "wrong"}).status_code == 401
    for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        assert client.request(method, "/projects").status_code == 405
    monkeypatch.delenv("MCP_OBSERVER_TOKEN")
    assert client.get("/health").status_code == 503


@pytest.mark.parametrize("wal", [False, True])
def test_observer_no_database_or_file_side_effects(observer, wal):
    client, path = observer
    client.cookies.set("story_manga_session", "expired-session-must-remain")
    with closing(sqlite3.connect(path)) as writer:
        if wal:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("UPDATE projects SET status='processing'")
            writer.commit()

        def hashes():
            return {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in path.parent.iterdir()
                if p.is_file()
            }

        before = hashes()
        for route in (
            "/health",
            "/projects",
            "/generation-jobs",
            "/exports",
            "/activity",
        ):
            response = client.get(route)
            assert response.status_code == 200
            assert "PRIVATE-CONTENT" not in response.text
            assert str(path) not in response.text
        assert hashes() == before
        assert (
            writer.execute("SELECT token FROM sessions").fetchone()[0]
            == "expired-session-must-remain"
        )
        with closing(connect_read_only(path)) as readonly:
            for sql in (
                "DELETE FROM projects",
                "UPDATE projects SET status='failed'",
                "PRAGMA user_version=42",
                "CREATE TABLE evil(id)",
                "SELECT token FROM sessions",
                "SELECT private_text FROM projects",
            ):
                with pytest.raises(sqlite3.DatabaseError):
                    readonly.execute(sql)


def test_observer_projection_and_pagination(observer):
    client, _ = observer
    for table, fields in FIELDS.items():
        first = client.get(f"/tables/{table}", params={"limit": 25}).json()
        second = client.get(
            f"/tables/{table}", params={"limit": 25, "offset": first["next_offset"]}
        ).json()
        assert first["count"] == 25 and second["count"] == 3
        assert second["next_offset"] is None
        assert set(first["rows"][0]) == set(fields)
        assert not {r["id"] for r in first["rows"]} & {r["id"] for r in second["rows"]}


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 26},
        {"limit": 0},
        {"offset": 10001},
        {"sql": "DROP TABLE projects"},
        {"columns": '["private_text"]'},
        {"filters": '[{"field":"private_text","operator":"eq","value":"x"}]'},
        {"order": '[{"field":"id","direction":"desc;DELETE"}]'},
        {"start_at": "2026-01-01", "end_at": "2026-02-01"},
        {"start_at": "not-date"},
        {"filters": "{"},
        {"query": "x" * 9000},
    ],
)
def test_observer_rejects_unsafe_queries(observer, params):
    client, _ = observer
    response = client.get("/projects", params=params)
    assert response.status_code == 400
    assert "DROP" not in response.text and "private_text" not in response.text


def test_observer_binds_values_and_denies_other_tables(observer):
    client, _ = observer
    assert client.get("/tables/sessions").status_code == 403
    result = client.get(
        "/projects",
        params={
            "filters": json.dumps(
                [{"field": "id", "operator": "eq", "value": "' OR 1=1 --"}]
            )
        },
    )
    assert result.status_code == 200 and result.json()["rows"] == []


def test_observer_does_not_create_missing_database(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_OBSERVER_TOKEN", "synthetic-observer-token-for-tests-only")
    path = tmp_path / "missing.db"
    client = TestClient(create_observer_app(lambda: path))
    assert (
        client.get(
            "/health",
            headers={"Authorization": "Bearer synthetic-observer-token-for-tests-only"},
        ).status_code
        == 503
    )
    assert not path.exists()


def test_observer_startup_keeper_and_shutdown(observer, monkeypatch):
    from app import db, observability

    _, path = observer
    path = path.with_name("startup.sqlite3")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(path))
    app = FastAPI()
    prepare = observability.install_observer(app)
    db.init_db(on_ready=prepare)
    original = path.read_bytes()
    with TestClient(app) as client:
        assert app.state.observer_keeper is not None
        before = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
        response = client.get(
            "/api/observer/projects",
            headers={"Authorization": "Bearer synthetic-observer-token-for-tests-only"},
        )
        assert response.status_code == 200
        assert before == {
            p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()
        }
        assert path.read_bytes() == original
    assert app.state.observer_keeper is None


def test_observer_masks_corrupt_allowed_values(observer):
    client, path = observer
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE projects SET status='PRIVATE-CONTENT', current_step='PRIVATE-CONTENT'"
        )
        connection.commit()
    response = client.get("/projects")
    assert response.status_code == 200 and "PRIVATE-CONTENT" not in response.text
    assert response.json()["rows"][0]["status"] == "unknown"
