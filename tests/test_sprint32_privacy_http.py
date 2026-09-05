import os
os.environ.setdefault("X1_DATABASE_AUTO_CREATE_SCHEMA", "true")

from fastapi.testclient import TestClient
from app.main import app


def test_robots_blocks_private_surfaces():
    with TestClient(app) as client:
        response = client.get('/robots.txt')
        assert response.status_code == 200
        assert 'Disallow: /v1/' in response.text
        assert 'Disallow: /admin' in response.text


def test_private_api_has_noindex_even_when_unauthorized():
    with TestClient(app) as client:
        response = client.get('/v1/conversations')
        assert response.headers['x-robots-tag'].startswith('noindex')
        assert 'no-store' in response.headers['cache-control']
