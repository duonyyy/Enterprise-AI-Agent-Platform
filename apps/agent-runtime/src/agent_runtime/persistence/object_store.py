"""Async S3/MinIO SigV4 object store adapter for immutable run data (06-A-T02)."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlparse
from uuid import UUID

import httpx

from agent_runtime.application.run_data import (
    DEFAULT_CONTENT_TYPE,
    RunDataConflictError,
    RunDataIntegrityError,
    RunDataNotFoundError,
    RunDataStorageUnavailableError,
    RunDataValidationError,
    StoredObjectReceipt,
)
from agent_runtime.settings import RuntimeSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InventoryObject:
    key: str
    last_modified: dt.datetime


@dataclass(frozen=True, slots=True)
class ObjectInventoryPage:
    objects: tuple[InventoryObject, ...]
    truncated: bool


class _ObjectReadLimitExceeded(RunDataValidationError):
    """Internal signal: an existing object's bytes exceed the bounded read."""


def _sign_s3_v4_headers(
    *,
    method: str,
    host: str,
    uri_path: str,
    payload: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    content_type: str | None = None,
    if_none_match: str | None = None,
    canonical_query: str = "",
) -> dict[str, str]:
    now = dt.datetime.now(dt.UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()

    headers_to_sign: list[tuple[str, str]] = []
    if content_type is not None:
        headers_to_sign.append(("content-type", content_type))
    if if_none_match is not None:
        headers_to_sign.append(("if-none-match", if_none_match))
    headers_to_sign.extend(
        [
            ("host", host),
            ("x-amz-content-sha256", payload_hash),
            ("x-amz-date", amz_date),
        ]
    )
    headers_to_sign.sort(key=lambda item: item[0])

    canonical_headers = "".join(f"{k}:{v}\n" for k, v in headers_to_sign)
    signed_headers = ";".join(k for k, _ in headers_to_sign)
    canonical_request = f"{method}\n{uri_path}\n{canonical_query}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            algorithm,
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, "s3")
    k_signing = _hmac(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    out = {
        "Host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": (
            f"{algorithm} Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }
    if content_type is not None:
        out["Content-Type"] = content_type
    if if_none_match is not None:
        out["If-None-Match"] = if_none_match
    return out


def _validate_bucket_and_key(bucket: str, key: str) -> tuple[str, str]:
    clean_bucket = bucket.strip()
    clean_key = key.lstrip("/")
    if (
        not clean_bucket
        or "/" in clean_bucket
        or "\\" in clean_bucket
        or ".." in clean_bucket
        or not clean_key
        or "\\" in clean_key
        or ".." in clean_key
        or "\x00" in clean_key
    ):
        raise RunDataValidationError("Invalid S3 bucket or object key")
    return clean_bucket, clean_key


class S3SigV4ObjectStore:
    """Async S3/MinIO client with fail-closed readiness and read-back verification."""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
        owns_client: bool | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket.strip()
        self._region = region
        self._timeout_seconds = timeout_seconds
        self._owns_client = (client is None) if owns_client is None else owns_client
        self._client = client

        parsed = urlparse(self._endpoint) if self._endpoint else None
        self._host = parsed.netloc if parsed else ""

    @property
    def default_bucket(self) -> str:
        return self._bucket

    @property
    def is_configured(self) -> bool:
        return bool(
            self._endpoint and self._host and self._access_key and self._secret_key and self._bucket
        )

    def _ensure_ready(self) -> httpx.AsyncClient:
        if not self.is_configured:
            raise RunDataStorageUnavailableError("Object storage is not configured")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def list_scoped_run_objects(
        self, *, tenant_id: str, workspace_id: str, limit: int = 100
    ) -> ObjectInventoryPage:
        """One bounded, read-only ListObjectsV2 page; never a whole-bucket scan."""
        if not 1 <= limit <= 100:
            raise RunDataValidationError("Invalid inventory limit")
        prefix = f"tenants/{UUID(tenant_id)}/workspaces/{UUID(workspace_id)}/runs/"
        query = urlencode(
            sorted(
                {
                    "encoding-type": "url",
                    "list-type": "2",
                    "max-keys": str(limit),
                    "prefix": prefix,
                }.items()
            ),
            quote_via=quote,
            safe="~",
        )
        client = self._ensure_ready()
        uri_path = f"/{quote(self._bucket, safe='-_.~')}"
        headers = _sign_s3_v4_headers(
            method="GET",
            host=self._host,
            uri_path=uri_path,
            payload=b"",
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
            canonical_query=query,
        )
        chunks: list[bytes] = []
        size = 0
        try:
            async with client.stream(
                "GET", f"{self._endpoint}{uri_path}?{query}", headers=headers
            ) as response:
                if response.status_code != 200:
                    raise RunDataStorageUnavailableError("Object inventory unavailable")
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 1_048_576:
                        raise RunDataStorageUnavailableError("Object inventory exceeds bound")
                    chunks.append(chunk)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise RunDataStorageUnavailableError("Object inventory unavailable") from exc
        body = b"".join(chunks)
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise RunDataStorageUnavailableError("Invalid object inventory")
        try:
            root = ET.fromstring(body)
            objects = []
            for item in root.findall("./{*}Contents"):
                key = unquote(item.findtext("{*}Key") or "")
                modified = dt.datetime.fromisoformat(item.findtext("{*}LastModified") or "")
                if (
                    not key.startswith(prefix)
                    or len(key.encode("utf-8")) > 1024
                    or modified.tzinfo is None
                ):
                    raise ValueError("Invalid inventory item")
                objects.append(InventoryObject(key, modified.astimezone(dt.UTC)))
            truncated_text = root.findtext("{*}IsTruncated")
            if len(objects) > limit or truncated_text not in ("true", "false"):
                raise ValueError("Invalid inventory page")
            return ObjectInventoryPage(tuple(objects), truncated_text == "true")
        except (ET.ParseError, ValueError) as exc:
            raise RunDataStorageUnavailableError("Invalid object inventory") from exc

    def _uri_path(self, bucket: str, key: str) -> str:
        encoded_key = quote(key, safe="/-_.~")
        return f"/{bucket}/{encoded_key}"

    async def _request(
        self,
        method: str,
        bucket: str,
        key: str,
        *,
        payload: bytes = b"",
        content_type: str | None = None,
        if_none_match: str | None = None,
    ) -> httpx.Response:
        clean_bucket, clean_key = _validate_bucket_and_key(bucket, key)
        client = self._ensure_ready()
        uri_path = self._uri_path(clean_bucket, clean_key)
        headers = _sign_s3_v4_headers(
            method=method,
            host=self._host,
            uri_path=uri_path,
            payload=payload,
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
            content_type=content_type,
            if_none_match=if_none_match,
        )
        try:
            return await client.request(
                method,
                f"{self._endpoint}{uri_path}",
                headers=headers,
                content=payload if payload else None,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("Object store %s failed (%s)", method, type(exc).__name__)
            raise RunDataStorageUnavailableError("Object storage request failed") from exc

    async def put_immutable_object(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        expected_sha256: str,
        content_type: str = DEFAULT_CONTENT_TYPE,
    ) -> StoredObjectReceipt:
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != expected_sha256:
            raise RunDataIntegrityError("Input bytes do not match expected SHA-256 before upload")

        clean_bucket, clean_key = _validate_bucket_and_key(bucket, key)
        resp = await self._request(
            "PUT",
            clean_bucket,
            clean_key,
            payload=data,
            content_type=content_type,
            if_none_match="*",
        )
        if resp.status_code in (409, 412):
            # Object already exists: allow idempotent replay only if stored bytes match exact hash/size.
            try:
                existing_bytes = await self.get_verified_object(
                    bucket=clean_bucket,
                    key=clean_key,
                    max_bytes=max(len(data), 1),
                )
            except _ObjectReadLimitExceeded as exc:
                # Larger existing bytes cannot match this replay. Preserve the
                # read bound while returning the same conflict as a hash mismatch.
                raise RunDataConflictError(
                    "Immutable object already exists with different content"
                ) from exc
            existing_sha256 = hashlib.sha256(existing_bytes).hexdigest()
            if existing_sha256 != expected_sha256 or len(existing_bytes) != len(data):
                raise RunDataConflictError("Immutable object already exists with different content")
            return StoredObjectReceipt(
                ref=f"s3://{clean_bucket}/{clean_key}",
                bucket=clean_bucket,
                key=clean_key,
                sha256=existing_sha256,
                size_bytes=len(existing_bytes),
                content_type=content_type,
            )
        if resp.status_code not in (200, 201):
            raise RunDataStorageUnavailableError(
                f"Object storage PUT failed with status {resp.status_code}"
            )

        # Verify actual readability, byte size, and SHA-256 digest before DB activation.
        verified_bytes = await self.get_verified_object(
            bucket=clean_bucket,
            key=clean_key,
            expected_sha256=expected_sha256,
            expected_size_bytes=len(data),
            max_bytes=max(len(data) * 2, 1024),
        )
        return StoredObjectReceipt(
            ref=f"s3://{clean_bucket}/{clean_key}",
            bucket=clean_bucket,
            key=clean_key,
            sha256=expected_sha256,
            size_bytes=len(verified_bytes),
            content_type=content_type,
        )

    async def get_verified_object(
        self,
        *,
        bucket: str,
        key: str,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        """Retry one interrupted GET, within one overall read budget; never retry PUT."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                for attempt in range(2):
                    try:
                        return await self._get_verified_object_once(
                            bucket=bucket,
                            key=key,
                            expected_sha256=expected_sha256,
                            expected_size_bytes=expected_size_bytes,
                            max_bytes=max_bytes,
                        )
                    except httpx.RemoteProtocolError as exc:
                        if attempt == 1:
                            raise RunDataStorageUnavailableError("Object read interrupted") from exc
        except TimeoutError as exc:
            raise RunDataStorageUnavailableError("Object read timed out") from exc
        raise RunDataStorageUnavailableError("Object read unavailable")

    async def _get_verified_object_once(
        self,
        *,
        bucket: str,
        key: str,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        clean_bucket, clean_key = _validate_bucket_and_key(bucket, key)
        client = self._ensure_ready()
        uri_path = self._uri_path(clean_bucket, clean_key)
        headers = _sign_s3_v4_headers(
            method="GET",
            host=self._host,
            uri_path=uri_path,
            payload=b"",
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
        )
        chunks: list[bytes] = []
        total_bytes = 0
        hasher = hashlib.sha256()
        try:
            async with client.stream(
                "GET",
                f"{self._endpoint}{uri_path}",
                headers=headers,
            ) as resp:
                if resp.status_code == 404:
                    raise RunDataNotFoundError("Referenced object does not exist in storage")
                if resp.status_code != 200:
                    raise RunDataStorageUnavailableError(
                        f"Object storage GET failed with status {resp.status_code}"
                    )

                content_len_raw = resp.headers.get("content-length")
                if (
                    max_bytes is not None
                    and content_len_raw is not None
                    and content_len_raw.isdigit()
                    and int(content_len_raw) > max_bytes
                ):
                    raise _ObjectReadLimitExceeded(
                        f"Stored object exceeds max allowed size ({content_len_raw} > {max_bytes} bytes)"
                    )

                async for chunk in resp.aiter_bytes():
                    total_bytes += len(chunk)
                    if max_bytes is not None and total_bytes > max_bytes:
                        raise _ObjectReadLimitExceeded(
                            f"Stored object exceeds max allowed size (> {max_bytes} bytes)"
                        )
                    hasher.update(chunk)
                    chunks.append(chunk)
        except httpx.RemoteProtocolError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("Object store GET failed (%s)", type(exc).__name__)
            raise RunDataStorageUnavailableError("Object storage request failed") from exc

        if expected_size_bytes is not None and total_bytes != expected_size_bytes:
            raise RunDataIntegrityError("Stored object size mismatch")
        if expected_sha256 is not None and hasher.hexdigest() != expected_sha256:
            raise RunDataIntegrityError("Stored object SHA-256 mismatch")
        return b"".join(chunks)

    async def head_object(self, *, bucket: str, key: str) -> dict[str, Any] | None:
        clean_bucket, clean_key = _validate_bucket_and_key(bucket, key)
        resp = await self._request("HEAD", clean_bucket, clean_key)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RunDataStorageUnavailableError(
                f"Object storage HEAD failed with status {resp.status_code}"
            )
        return {
            "content_length": int(resp.headers.get("content-length", "0")),
            "etag": resp.headers.get("etag", "").strip('"'),
            "content_type": resp.headers.get("content-type", ""),
        }

    async def delete_object(self, *, bucket: str, key: str) -> bool:
        clean_bucket, clean_key = _validate_bucket_and_key(bucket, key)
        resp = await self._request("DELETE", clean_bucket, clean_key)
        if resp.status_code in (200, 204):
            return True
        if resp.status_code == 404:
            return False
        raise RunDataStorageUnavailableError(
            f"Object storage DELETE failed with status {resp.status_code}"
        )


def create_object_store(settings: RuntimeSettings) -> S3SigV4ObjectStore:
    """Create the process-owned S3/MinIO object store adapter from runtime settings."""
    client = (
        httpx.AsyncClient(timeout=settings.s3_timeout_seconds)
        if settings.s3_endpoint and settings.s3_access_key and settings.s3_secret_key
        else None
    )
    return S3SigV4ObjectStore(
        endpoint=settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        bucket=settings.s3_bucket,
        region=settings.s3_region,
        timeout_seconds=settings.s3_timeout_seconds,
        client=client,
        owns_client=client is not None,
    )


def probe_object_store_sync(
    settings: RuntimeSettings,
    *,
    timeout_seconds: float = 0.8,
) -> bool:
    """Bounded synchronous SigV4 HEAD /<bucket> readiness check (never leaks secrets)."""
    endpoint = settings.s3_endpoint.rstrip("/")
    bucket = settings.s3_bucket.strip()
    parsed = urlparse(endpoint) if endpoint else None
    host = parsed.netloc if parsed else ""
    if not (endpoint and host and settings.s3_access_key and settings.s3_secret_key and bucket):
        return False
    if "/" in bucket or "\\" in bucket or ".." in bucket:
        return False
    uri_path = f"/{bucket}"
    headers = _sign_s3_v4_headers(
        method="HEAD",
        host=host,
        uri_path=uri_path,
        payload=b"",
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )
    try:
        with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
            resp = client.head(f"{endpoint}{uri_path}", headers=headers)
            return bool(resp.status_code == 200)
    except (httpx.TimeoutException, httpx.TransportError, OSError, ValueError):
        return False
