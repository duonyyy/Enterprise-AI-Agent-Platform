"""HTTP integration tests for If-Match optimistic concurrency parser."""

from __future__ import annotations

import pytest
from control_api.core.concurrency import IfMatchRevision
from fastapi import FastAPI
from starlette.testclient import TestClient


def create_test_app() -> FastAPI:
    app = FastAPI()

    @app.patch("/test-resource")
    def patch_resource(revision: IfMatchRevision):
        return {"expected_revision": revision, "status": "ok"}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_test_app())


def test_missing_if_match_returns_428(client: TestClient):
    response = client.patch("/test-resource")
    assert response.status_code == 428
    body = response.json()
    assert body["detail"]["code"] == "precondition_required"
    assert "If-Match header is required" in body["detail"]["message"]


@pytest.mark.parametrize("header", ["", " ", "\t"])
def test_empty_if_match_returns_400(client: TestClient, header: str) -> None:
    response = client.patch("/test-resource", headers={"If-Match": header})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] == "invalid_if_match"


@pytest.mark.parametrize("revision", [2147483648, 9223372036854775807, 9223372036854775808])
def test_revision_outside_postgres_integer_returns_400(client: TestClient, revision: int) -> None:
    response = client.patch("/test-resource", headers={"If-Match": f'"{revision}"'})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_if_match"


def test_postgres_integer_max_revision_is_accepted(client: TestClient) -> None:
    response = client.patch("/test-resource", headers={"If-Match": '"2147483647"'})
    assert response.status_code == 200
    assert response.json()["expected_revision"] == 2147483647


def test_duplicate_if_match_headers_are_rejected(client: TestClient) -> None:
    response = client.patch("/test-resource", headers=[("if-match", '"1"'), ("IF-MATCH", '"2"')])
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_if_match"


def test_wildcard_if_match_returns_400(client: TestClient):
    response = client.patch("/test-resource", headers={"If-Match": "*"})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] == "invalid_if_match"
    assert "Wildcard" in body["detail"]["message"]


def test_weak_etag_returns_400(client: TestClient):
    response = client.patch("/test-resource", headers={"If-Match": 'W/"3"'})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] == "invalid_if_match"
    assert "Weak entity-tags" in body["detail"]["message"]


def test_lowercase_weak_etag_returns_400(client: TestClient):
    response = client.patch("/test-resource", headers={"If-Match": 'w/"3"'})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] == "invalid_if_match"


def test_list_etag_returns_400(client: TestClient):
    response = client.patch("/test-resource", headers={"If-Match": '"1", "2"'})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] == "invalid_if_match"
    assert "Multiple or comma-separated" in body["detail"]["message"]


def test_unquoted_integer_returns_400(client: TestClient):
    response = client.patch("/test-resource", headers={"If-Match": "3"})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] == "invalid_if_match"


def test_zero_or_negative_revision_returns_400(client: TestClient):
    for bad in ('"0"', '"-1"', '"-42"'):
        response = client.patch("/test-resource", headers={"If-Match": bad})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_if_match"


def test_non_numeric_etag_returns_400(client: TestClient):
    response = client.patch("/test-resource", headers={"If-Match": '"v1.0"'})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_if_match"


def test_valid_strong_etag_returns_parsed_integer(client: TestClient):
    response = client.patch("/test-resource", headers={"If-Match": '"1"'})
    assert response.status_code == 200
    assert response.json() == {"expected_revision": 1, "status": "ok"}

    response42 = client.patch("/test-resource", headers={"If-Match": '"42"'})
    assert response42.status_code == 200
    assert response42.json() == {"expected_revision": 42, "status": "ok"}


def test_case_insensitive_header_lookup(client: TestClient):
    response = client.patch("/test-resource", headers={"if-match": '"7"'})
    assert response.status_code == 200
    assert response.json()["expected_revision"] == 7
