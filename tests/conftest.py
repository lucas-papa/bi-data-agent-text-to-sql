"""Shared pytest fixtures for database seeding and FastAPI TestClient."""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.config import Settings, get_settings
from backend.main import app
from fastapi.testclient import TestClient
from scripts.seed_db import seed_database


@pytest.fixture(scope="session")
def seeded_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a deterministic seeded SQLite warehouse for the test session."""
    db_dir = tmp_path_factory.mktemp("warehouse_data")
    db_file = db_dir / "test_warehouse.db"
    seed_database(db_file, seed=42)
    return db_file


@pytest.fixture()
def test_settings(seeded_db_path: Path) -> Settings:
    """Provide Settings pointing to the isolated test SQLite database."""
    return Settings(
        gemini_api_key=None,
        gemini_model="gemini-3.8-flash",
        database_path=seeded_db_path,
        max_query_rows=200,
        max_agent_turns=8,
    )


@pytest.fixture()
def api_client(test_settings: Settings) -> TestClient:
    """Return a FastAPI TestClient overriding get_settings with test_settings."""
    app.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
