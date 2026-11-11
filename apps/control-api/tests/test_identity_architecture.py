"""Clean Architecture boundary, domain exception, and StrEnum tests for Identity."""

from __future__ import annotations

import ast
import asyncio
import time
from enum import StrEnum
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app, repository_unavailable_handler, session_expired_handler
from control_api.modules.identity.models.errors import (
    AuthorizationDatabaseUnavailableError,
    AuthTransactionStoreUnavailableError,
    IdentityError,
    RepositoryUnavailableError,
    RevocationStoreUnavailableError,
    SessionExpiredError,
)
from control_api.modules.identity.models.session import (
    AuthTransaction,
    SessionVerificationStatus,
)
from control_api.modules.identity.providers.oidc_client import OidcClient
from control_api.modules.identity.repositories.auth_transaction_repository import (
    BaseAuthTransactionStore,
    RedisAuthTransactionStore,
)
from control_api.modules.identity.repositories.memory_session_store import (
    InMemoryRevocationStore,
)
from control_api.modules.identity.repositories.session_repository import RedisRevocationStore
from control_api.modules.identity.repositories.user_repository import user_repository
from control_api.modules.identity.services.auth_service import identity_service
from control_api.modules.identity.services.session_service import session_manager
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request


def _sample_auth_tx(state: str = "state-arch-1") -> AuthTransaction:
    return AuthTransaction(
        state=state,
        nonce="nonce-arch-1",
        code_verifier="verifier-arch-1",
        code_challenge="challenge-arch-1",
        return_to="/w/ws-alpha/agents",
        browser_correlation="corr-arch-1",
        created_at=int(time.time()),
    )


def _dummy_request(path: str = "/v1/auth/me") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
    }
    return Request(scope)


def _failing_redis_client() -> MagicMock:
    client = MagicMock()
    err = RedisError("simulated redis outage")
    client.get.side_effect = err
    client.set.side_effect = err
    client.setex.side_effect = err
    client.getdel.side_effect = err
    client.delete.side_effect = err
    client.eval.side_effect = err
    client.evalsha.side_effect = err
    client.script_load.side_effect = err
    client.register_script.side_effect = lambda _script: MagicMock(side_effect=err)
    return client


def test_identity_repositories_adapters_and_models_do_not_import_fastapi() -> None:
    identity_dir = (
        Path(__file__).resolve().parents[1] / "src" / "control_api" / "modules" / "identity"
    )
    pure_layers = ("repositories", "adapters", "models")
    violations: list[str] = []

    for layer in pure_layers:
        for py_file in sorted((identity_dir / layer).glob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "fastapi" or alias.name.startswith("fastapi."):
                            violations.append(f"{py_file.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod == "fastapi" or mod.startswith("fastapi."):
                        violations.append(f"{py_file.name}: from {mod} import ...")

    assert violations == []


def test_session_verification_status_is_strenum_with_expected_values() -> None:
    assert issubclass(SessionVerificationStatus, StrEnum)
    assert [member.value for member in SessionVerificationStatus] == [
        "OK",
        "REVOKED",
        "MISSING",
        "CORRUPT",
        "VERSION_MISMATCH",
        "ABSOLUTE_EXPIRED",
        "IDLE_EXPIRED",
    ]
    assert SessionVerificationStatus.OK == "OK"


def test_user_repository_raises_pure_domain_error_on_db_failure() -> None:
    db_session = AsyncMock()
    db_session.execute.side_effect = SQLAlchemyError("db connection lost")

    async def _exercise() -> None:
        with pytest.raises(AuthorizationDatabaseUnavailableError) as exc_info:
            await user_repository.get_by_id(db_session, str(uuid4()))
        assert isinstance(exc_info.value, RepositoryUnavailableError)
        assert not isinstance(exc_info.value, HTTPException)

        with pytest.raises(AuthorizationDatabaseUnavailableError) as ws_exc_info:
            await user_repository.get_user_workspaces(db_session, str(uuid4()))
        assert isinstance(ws_exc_info.value, RepositoryUnavailableError)
        assert not isinstance(ws_exc_info.value, HTTPException)

    asyncio.run(_exercise())


def test_redis_revocation_store_raises_pure_domain_error_on_redis_failure() -> None:
    failing_client = _failing_redis_client()
    with patch("redis.Redis.from_url", return_value=failing_client):
        store = RedisRevocationStore("redis://localhost:6379/15")

    with pytest.raises(RevocationStoreUnavailableError) as exc_info:
        store.revoke_session("sid-arch-1", expires_at=int(time.time()) + 60)

    assert isinstance(exc_info.value, RepositoryUnavailableError)
    assert isinstance(exc_info.value, IdentityError)
    assert not isinstance(exc_info.value, HTTPException)


def test_redis_auth_transaction_store_raises_pure_domain_error_on_redis_failure() -> None:
    failing_client = _failing_redis_client()
    with patch("redis.Redis.from_url", return_value=failing_client):
        store = RedisAuthTransactionStore("redis://localhost:6379/15")

    with pytest.raises(AuthTransactionStoreUnavailableError) as save_exc:
        store.save(_sample_auth_tx("state-save-fail"))
    assert isinstance(save_exc.value, RepositoryUnavailableError)
    assert not isinstance(save_exc.value, HTTPException)

    with pytest.raises(AuthTransactionStoreUnavailableError) as consume_exc:
        store.consume("state-consume-fail")
    assert isinstance(consume_exc.value, RepositoryUnavailableError)
    assert not isinstance(consume_exc.value, HTTPException)


def test_global_exception_handlers_directly_map_domain_errors() -> None:
    async def _exercise() -> None:
        resp_503 = await repository_unavailable_handler(
            _dummy_request(),
            AuthorizationDatabaseUnavailableError("db unreachable"),
        )
        assert resp_503.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert b"db unreachable" in resp_503.body

        resp_401 = await session_expired_handler(
            _dummy_request("/v1/auth/refresh"),
            SessionExpiredError("Session idle expired"),
        )
        assert resp_401.status_code == status.HTTP_401_UNAUTHORIZED
        assert resp_401.headers.get("WWW-Authenticate") == "Bearer"
        assert b"Session idle expired" in resp_401.body

    asyncio.run(_exercise())


def test_fastapi_app_handlers_and_logout_cookie_clear_with_domain_errors() -> None:
    orig_denylist = session_manager.denylist
    session_manager.denylist = InMemoryRevocationStore()
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    try:
        user_id = str(uuid4())
        _session, session_token = session_manager.create_session(
            user_id,
            settings.oidc_issuer,
            "sub-arch-1",
        )
        client = TestClient(app, base_url="http://localhost:8000", raise_server_exceptions=False)
        client.cookies.set(settings.session_cookie_name, session_token)

        with patch.object(
            identity_service,
            "get_user_by_id",
            side_effect=AuthorizationDatabaseUnavailableError("authz postgres down"),
        ):
            me_resp = client.get("/v1/auth/me")
            assert me_resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            assert me_resp.json()["detail"] == "authz postgres down"

        with patch.object(
            session_manager,
            "refresh_session",
            side_effect=SessionExpiredError("Session rotated or missing"),
        ):
            refresh_resp = client.post(
                "/v1/auth/refresh",
                headers={"Origin": "http://localhost:8000"},
            )
            assert refresh_resp.status_code == status.HTTP_401_UNAUTHORIZED
            assert refresh_resp.headers.get("www-authenticate") == "Bearer"
            assert refresh_resp.json()["detail"] == "Session rotated or missing"

        with patch.object(
            session_manager,
            "revoke_session_by_token",
            side_effect=RevocationStoreUnavailableError("redis unavailable"),
        ):
            logout_resp = client.post(
                "/v1/auth/logout",
                headers={"Origin": "http://localhost:8000"},
            )
            assert logout_resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            set_cookie = logout_resp.headers.get("set-cookie", "")
            assert f"{settings.session_cookie_name}=" in set_cookie
            assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()
    finally:
        app.dependency_overrides.clear()
        session_manager.denylist = orig_denylist


def test_oidc_client_translates_transaction_store_outages_to_http_503() -> None:
    class _FailingTxStore(BaseAuthTransactionStore):
        def save(self, tx: AuthTransaction) -> None:
            raise AuthTransactionStoreUnavailableError("redis tx write failed")

        def consume(self, state: str) -> AuthTransaction | None:
            raise AuthTransactionStoreUnavailableError("redis tx consume failed")

        def clear_all(self) -> None:
            return None

    client = OidcClient(transaction_store=_FailingTxStore())

    with pytest.raises(HTTPException) as init_exc:
        client.initiate_login(return_to="/w/default/agents", browser_correlation="corr-1")
    assert init_exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    with pytest.raises(HTTPException) as consume_exc:
        client.consume_transaction("state-1")
    assert consume_exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    async def _exercise_callback() -> Any:
        return await client.handle_callback(
            code="code-1",
            state="state-1",
            browser_correlation="corr-1",
        )

    with pytest.raises(HTTPException) as cb_exc:
        asyncio.run(_exercise_callback())
    assert cb_exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
