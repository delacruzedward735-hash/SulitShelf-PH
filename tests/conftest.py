import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.services.billing import ensure_defaults


@pytest.fixture()
def app(tmp_path):
    class LocalTestConfig(TestConfig):
        UPLOAD_ROOT = tmp_path / "uploads"
    app = create_app(LocalTestConfig)
    with app.app_context():
        db.create_all()
        ensure_defaults()
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()
