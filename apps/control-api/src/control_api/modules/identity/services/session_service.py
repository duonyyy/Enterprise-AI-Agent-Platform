"""Session management service for Enterprise Agent Platform (SPEC-AUTH-001).

Coordinates session lifecycle: creation, JWT signing, authoritative store verification,
atomic single-winner rotation, and multi-level revocation.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any, ClassVar
from uuid import UUID, uuid4

from control_api.core.settings import settings
from control_api.modules.identity.models.errors import (
    RepositoryUnavailableError,
    SessionExpiredError,
)
from control_api.modules.identity.models.session import (
    SessionData,
    SessionVerificationStatus,
    StoredSessionState,
)
from control_api.modules.identity.repositories.session_repository import (
    BaseRevocationStore,
    revocation_denylist,
)
from control_api.modules.identity.security.crypto import sign_jwt_hs256, verify_jwt_hs256
from fastapi import HTTPException, status


def _translate_store_errors[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except RepositoryUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except SessionExpiredError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    return wrapper


def _strict_positive_int(value: Any) -> int | None:
    if type(value) is not int or value <= 0:
        return None
    return value


class SessionManager:
    """Manages creation, signing, verification, atomic rotation, and revocation of sessions."""

    def __init__(self, denylist: BaseRevocationStore | None = None) -> None:
        self.denylist = denylist or revocation_denylist

    def _sign_session(self, session: SessionData) -> str:
        payload = {
            "type": "session",
            "session_id": session.session_id,
            "ver": session.rotation_version,
            "sub": session.user_id,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "original_iss": session.issuer,
            "original_sub": session.subject,
            "iat": session.created_at,
            "idle_exp": session.idle_expires_at,
            "exp": session.absolute_expires_at,
        }
        return sign_jwt_hs256(payload, settings.jwt_secret_key.get_secret_value())

    def _decode_and_validate_session_claims(self, token: str) -> SessionData:
        try:
            payload = verify_jwt_hs256(token, settings.jwt_secret_key.get_secret_value())
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token signature or format",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        if payload.get("type") != "session":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is not an application session token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if payload.get("iss") != settings.jwt_issuer:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token issuer",
                headers={"WWW-Authenticate": "Bearer"},
            )

        aud = payload.get("aud")
        audiences = [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
        if settings.jwt_audience not in audiences:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token audience",
                headers={"WWW-Authenticate": "Bearer"},
            )

        session_id = payload.get("session_id")
        user_id = payload.get("sub")
        original_iss = payload.get("original_iss")
        original_sub = payload.get("original_sub")
        ver = _strict_positive_int(payload.get("ver"))
        created_at = _strict_positive_int(payload.get("iat"))
        idle_exp = _strict_positive_int(payload.get("idle_exp"))
        abs_exp = _strict_positive_int(payload.get("exp"))

        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(user_id, str)
            or not user_id
            or not isinstance(original_iss, str)
            or not original_iss
            or not isinstance(original_sub, str)
            or not original_sub
            or ver is None
            or created_at is None
            or idle_exp is None
            or abs_exp is None
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session token missing essential claims",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            UUID(user_id)
            UUID(session_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session identifier claims",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        return SessionData(
            session_id=session_id,
            user_id=user_id,
            issuer=original_iss,
            subject=original_sub,
            created_at=created_at,
            idle_expires_at=idle_exp,
            absolute_expires_at=abs_exp,
            rotation_version=ver,
        )

    _STATUS_ERROR_DETAILS: ClassVar[dict[str, str]] = {
        SessionVerificationStatus.REVOKED: "Session has been revoked",
        SessionVerificationStatus.ABSOLUTE_EXPIRED: "Session absolute lifetime expired",
        SessionVerificationStatus.IDLE_EXPIRED: "Session idle timeout expired",
        SessionVerificationStatus.VERSION_MISMATCH: "Session credential has been superseded by rotation",
        SessionVerificationStatus.CORRUPT: "Session state is invalid or corrupted",
    }

    def _raise_for_store_status(self, status_code: str) -> None:
        detail = self._STATUS_ERROR_DETAILS.get(status_code, "Session state not found or expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @_translate_store_errors
    def create_session(
        self,
        user_id: str,
        issuer: str,
        subject: str,
        now: int | None = None,
    ) -> tuple[SessionData, str]:
        current = now if now is not None else int(time.time())
        session_id = str(uuid4())
        UUID(user_id)

        idle_timeout = settings.session_idle_timeout_seconds
        absolute_timeout = settings.session_absolute_timeout_seconds

        idle_expires_at = min(current + idle_timeout, current + absolute_timeout)
        absolute_expires_at = current + absolute_timeout
        rotation_version = 1

        session = SessionData(
            session_id=session_id,
            user_id=user_id,
            issuer=issuer,
            subject=subject,
            created_at=current,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            rotation_version=rotation_version,
        )

        # Sign credential first, then persist authoritative state in store before returning.
        # If Redis/store creation fails, it raises 503 and no session or cookie is issued.
        token = self._sign_session(session)
        state = StoredSessionState(
            session_id=session.session_id,
            user_id=session.user_id,
            issuer=session.issuer,
            subject=session.subject,
            rotation_version=session.rotation_version,
            created_at=session.created_at,
            idle_expires_at=session.idle_expires_at,
            absolute_expires_at=session.absolute_expires_at,
        )
        self.denylist.create_session_state(state, now=current)
        return session, token

    @_translate_store_errors
    def verify_session_token(self, token: str, now: int | None = None) -> SessionData:
        current = now if now is not None else int(time.time())
        token_session = self._decode_and_validate_session_claims(token)

        # Verify against authoritative store (revocation + existence + version + lifecycle)
        status_code, state = self.denylist.verify_and_get_session(
            session_id=token_session.session_id,
            user_id=token_session.user_id,
            expected_version=token_session.rotation_version,
            session_created_at=token_session.created_at,
            now=now,
        )
        if status_code != SessionVerificationStatus.OK or state is None:
            self._raise_for_store_status(status_code)
            raise AssertionError("unreachable")

        if (
            state.issuer != token_session.issuer
            or state.subject != token_session.subject
            or state.absolute_expires_at != token_session.absolute_expires_at
            or state.idle_expires_at != token_session.idle_expires_at
        ):
            self._raise_for_store_status(SessionVerificationStatus.CORRUPT)

        # Defense-in-depth local clock check when explicit clock is passed
        if token_session.is_absolute_expired(current):
            self._raise_for_store_status(SessionVerificationStatus.ABSOLUTE_EXPIRED)
        if token_session.is_idle_expired(current):
            self._raise_for_store_status(SessionVerificationStatus.IDLE_EXPIRED)

        return SessionData(
            session_id=state.session_id,
            user_id=state.user_id,
            issuer=state.issuer,
            subject=state.subject,
            created_at=state.created_at,
            idle_expires_at=state.idle_expires_at,
            absolute_expires_at=state.absolute_expires_at,
            rotation_version=state.rotation_version,
        )

    @_translate_store_errors
    def verify_parent_session_active(
        self,
        session_id: str,
        user_id: str,
        now: int | None = None,
    ) -> StoredSessionState:
        """Verify that a scoped workspace token's parent session is active and unrevoked (D3-B)."""
        try:
            UUID(session_id)
            UUID(user_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session identifier claims",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        status_code, state = self.denylist.verify_parent_session(
            session_id=session_id,
            user_id=user_id,
            now=now,
        )
        if status_code != SessionVerificationStatus.OK or state is None:
            self._raise_for_store_status(status_code)
            raise AssertionError("unreachable")
        return state

    @_translate_store_errors
    def rotate_session_by_token(
        self, token: str, now: int | None = None
    ) -> tuple[SessionData, str]:
        """Verify presented session token and atomically rotate it to the next credential version."""
        token_session = self._decode_and_validate_session_claims(token)
        return self.refresh_session(token_session, now=now)

    @_translate_store_errors
    def refresh_session(
        self, current_session: SessionData, now: int | None = None
    ) -> tuple[SessionData, str]:
        """Atomically rotate session state in store and issue a new credential with incremented version.

        Caller-supplied SessionData never bypasses the authoritative store check at rotation time.
        """
        idle_timeout = settings.session_idle_timeout_seconds
        status_code, updated_state = self.denylist.rotate_session_state(
            session_id=current_session.session_id,
            user_id=current_session.user_id,
            expected_version=current_session.rotation_version,
            session_created_at=current_session.created_at,
            idle_timeout_seconds=idle_timeout,
            now=now,
        )
        if status_code != SessionVerificationStatus.OK or updated_state is None:
            self._raise_for_store_status(status_code)
            raise AssertionError("unreachable")

        new_session = SessionData(
            session_id=updated_state.session_id,
            user_id=updated_state.user_id,
            issuer=updated_state.issuer,
            subject=updated_state.subject,
            created_at=updated_state.created_at,
            idle_expires_at=updated_state.idle_expires_at,
            absolute_expires_at=updated_state.absolute_expires_at,
            rotation_version=updated_state.rotation_version,
        )
        new_token = self._sign_session(new_session)
        return new_session, new_token

    @_translate_store_errors
    def revoke_session(self, session: SessionData, now: int | None = None) -> None:
        """Revoke a specific session until its absolute expiry and delete active state."""
        self.denylist.revoke_session(session.session_id, session.absolute_expires_at, now=now)

    @_translate_store_errors
    def revoke_user(
        self,
        user_id: str,
        cutoff: int | None = None,
        max_session_lifetime: int | None = None,
        now: int | None = None,
    ) -> None:
        """Revoke all active sessions for a user issued at or before cutoff timestamp."""
        current = now if now is not None else int(time.time())
        actual_cutoff = cutoff if cutoff is not None else current
        self.denylist.revoke_user(
            user_id,
            actual_cutoff,
            max_session_lifetime=max_session_lifetime,
            now=current,
        )

    @_translate_store_errors
    def revoke_session_by_token(self, token: str, now: int | None = None) -> None:
        """Revoke a session identified by a signed JWT session token (including superseded versions).

        - If token signature/claims are invalid or malformed: no-op (idempotent local cleanup).
        - If token is already absolute-expired (now >= exp): no-op (already unusable everywhere).
        - If token is already marked revoked: ensures active state is removed (idempotent).
        - If token is active, idle-expired, or an older rotated version of the session:
          writes revocation to denylist until absolute expiry and removes active session state.
        - If Redis fails: raises HTTPException(503).
        """
        current = now if now is not None else int(time.time())
        try:
            payload = verify_jwt_hs256(token, settings.jwt_secret_key.get_secret_value())
        except (ValueError, TypeError):
            return

        if payload.get("type") != "session":
            return
        if payload.get("iss") != settings.jwt_issuer:
            return
        aud = payload.get("aud")
        audiences = [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
        if settings.jwt_audience not in audiences:
            return

        session_id = payload.get("session_id")
        user_id = payload.get("sub")
        abs_exp = _strict_positive_int(payload.get("exp"))
        created_at = _strict_positive_int(payload.get("iat"))

        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(user_id, str)
            or not user_id
            or abs_exp is None
            or created_at is None
        ):
            return

        try:
            UUID(session_id)
            UUID(user_id)
        except ValueError:
            return

        if current >= abs_exp:
            return

        # Check existing revocation status (raises 503 if Redis is unavailable)
        if self.denylist.is_session_revoked(session_id, user_id, created_at, now=current):
            return

        # Write revocation marker and delete active session state (raises 503 if Redis is unavailable)
        self.denylist.revoke_session(session_id, abs_exp, now=current)


session_manager = SessionManager()
