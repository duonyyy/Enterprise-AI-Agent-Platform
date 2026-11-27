"""Allowlisted direct connections pinned to validated DNS results, preserving TLS SNI."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence

import httpx

from agent_runtime.llm.errors import ModelPolicyDeniedError, ModelUnavailableError

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


def validate_endpoint(url: str, *, allow_local: bool = False) -> httpx.URL:
    try:
        parsed = httpx.URL(url)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.host
            or parsed.userinfo
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError()
        if parsed.scheme == "http" and not allow_local:
            raise ModelPolicyDeniedError("Insecure HTTP scheme is prohibited")
        if "%" in parsed.host or "\\" in url:
            raise ValueError()
        try:
            ip = ipaddress.ip_address(parsed.host)
        except ValueError:
            if (
                parsed.host.rstrip(".").lower() in {"localhost", "localhost.localdomain"}
                and not allow_local
            ):
                raise ModelPolicyDeniedError("Local endpoint prohibited")
        else:
            if not allow_local and not public_address(str(ip)):
                raise ModelPolicyDeniedError("Non-public endpoint prohibited")
        return parsed
    except (httpx.InvalidURL, ValueError):
        raise ModelPolicyDeniedError("Invalid model endpoint") from None


def public_address(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_unspecified


async def resolve_addresses(host: str, port: int) -> Sequence[str]:
    try:
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ModelUnavailableError("Model DNS lookup failed") from None
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))


class PinnedEgressTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        endpoints: Sequence[str],
        *,
        allow_local: bool = False,
        resolver: Resolver = resolve_addresses,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoints = tuple(validate_endpoint(e, allow_local=allow_local) for e in endpoints)
        self._allow_local = allow_local
        self._resolver = resolver
        # Avoid reusing one IP's TLS connection for a different virtual host.
        self._transport = transport or httpx.AsyncHTTPTransport(
            retries=0,
            trust_env=False,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=0),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = validate_endpoint(str(request.url), allow_local=self._allow_local)
        if not any(
            (url.scheme, url.host, url.port, url.path)
            == (
                base.scheme,
                base.host,
                base.port,
                base.path
                if base.path.endswith("/chat/completions")
                else base.path.rstrip("/") + "/chat/completions",
            )
            for base in self._endpoints
        ):
            raise ModelPolicyDeniedError("Model endpoint is not allowlisted")
        try:
            addresses = await self._resolver(
                url.host, url.port or (443 if url.scheme == "https" else 80)
            )
            if (
                not addresses
                or any(not public_address(ip) for ip in addresses)
                and not self._allow_local
            ):
                raise ModelPolicyDeniedError("Model DNS resolved to a non-public destination")
            selected = str(ipaddress.ip_address(addresses[0]))
        except ValueError:
            raise ModelPolicyDeniedError("Invalid DNS address") from None
        headers = request.headers.copy()
        headers["Host"] = url.netloc.decode("ascii")
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=selected),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": url.host},
        )
        # The TCP target is a literal address; it cannot undergo another hostname
        # lookup. TLS still validates the configured hostname (httpcore extension).
        return await self._transport.handle_async_request(pinned)

    async def aclose(self) -> None:
        await self._transport.aclose()
