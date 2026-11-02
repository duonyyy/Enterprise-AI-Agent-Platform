"""
MCP Client Adapter with Cryptographic & Network Egress SSRF Hardening (SPEC-MCP-001, CR-MCP-001).
Enforces DNS resolution, IPv4/IPv6 private range checks, loopback prevention,
and strict approval binding verification before executing any remote MCP tool.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from agent_runtime.mcp.errors import (
    McpEndpointBlockedError,
    McpToolNotApprovedError,
    McpSchemaDriftError,
    McpConnectionDisabledError,
    McpRemoteTimeoutError,
)


class McpClientAdapter:
    """
    MCP Client Adapter.
    Executes remote Streamable HTTP MCP tools with strict SSRF filtering, timeout, and schema integrity guards.
    """

    def __init__(
        self,
        allow_localhost_dev: bool = False,
        dns_resolver: Optional[Callable[[str, int], List[str]]] = None,
    ):
        self.allow_localhost_dev = allow_localhost_dev
        self.dns_resolver = dns_resolver

    def _default_resolver(self, host: str, port: int) -> List[str]:
        try:
            addr_info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            return [item[4][0] for item in addr_info]
        except socket.gaierror as e:
            raise McpEndpointBlockedError(f"Failed to resolve hostname '{host}': {e}") from e

    def validate_endpoint(self, endpoint_url: str) -> bool:
        """
        Validates endpoint against SSRF and protocol spoofing using deep IP parsing & DNS resolution.
        Prevents integer IP bypasses (e.g. 2130706433), full RFC1918 (172.16.0.0/12), IPv6 loopback (::1),
        and cloud metadata service IP (169.254.169.254).
        """
        parsed = urlparse(endpoint_url)
        if parsed.scheme not in ("http", "https"):
            raise McpEndpointBlockedError(f"Disallowed scheme: '{parsed.scheme}'. Only HTTPS/HTTP permitted.")

        host = parsed.hostname
        if not host:
            raise McpEndpointBlockedError("Invalid URL: missing hostname")

        # Strip brackets if IPv6 literal
        clean_host = host.strip("[]")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        # Check if hostname itself is a raw integer, IPv4, or IPv6 literal
        resolved_ips: List[str] = []
        try:
            if clean_host.isdigit():
                # Integer IP bypass prevention (e.g. 2130706433 -> 127.0.0.1)
                ip_obj = ipaddress.ip_address(int(clean_host))
            else:
                ip_obj = ipaddress.ip_address(clean_host)
            resolved_ips.append(str(ip_obj))
        except ValueError:
            # If not an IP literal, resolve via DNS
            resolver = self.dns_resolver or self._default_resolver
            resolved_ips = resolver(clean_host, port)

        if not resolved_ips:
            raise McpEndpointBlockedError(f"Hostname '{clean_host}' could not be resolved to any IP address")

        for ip_str in resolved_ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError as exc:
                raise McpEndpointBlockedError(f"Invalid IP address resolved: '{ip_str}'") from exc

            if self.allow_localhost_dev and ip.is_loopback:
                continue

            # Strict check across all forbidden network ranges
            if (
                ip.is_loopback
                or ip.is_private
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise McpEndpointBlockedError(
                    f"Egress blocked by SSRF Defense: IP '{ip_str}' (resolved from '{clean_host}') "
                    f"falls into restricted/private network boundary."
                )

        return True

    async def call_tool(
        self,
        endpoint_url: str,
        tool_name: str,
        arguments: Dict[str, Any],
        approved_mapping: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Invokes an approved remote MCP Tool.
        Enforces approved status and schema fingerprint match.
        """
        # Step 1: Egress SSRF validation
        self.validate_endpoint(endpoint_url)

        # Step 2: Validate approval status
        if approved_mapping.get("status") != "approved":
            raise McpToolNotApprovedError(f"Tool '{tool_name}' is '{approved_mapping.get('status')}', not approved.")

        # Step 3: Validate connection is enabled
        if approved_mapping.get("connection_status") == "disabled":
            raise McpConnectionDisabledError("Upstream MCP connection has been disabled.")

        approved_hash = approved_mapping.get("approved_schema_sha256")
        discovered_hash = approved_mapping.get("discovered_schema_sha256")
        if not isinstance(approved_hash, str) or not approved_hash:
            raise McpSchemaDriftError("Approved mapping is missing a pinned schema hash")
        if approved_hash != discovered_hash:
            raise McpSchemaDriftError("Remote MCP schema hash no longer matches the reviewed mapping")

        timeout_seconds = approved_mapping.get("timeout_seconds", 10.0)
        if not isinstance(timeout_seconds, (float, int)) or timeout_seconds <= 0:
            raise McpEndpointBlockedError("Invalid reviewed MCP timeout")

        request_body = {
            "jsonrpc": "2.0",
            "id": approved_mapping.get("request_id", tool_name),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(float(timeout_seconds)),
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    endpoint_url,
                    json=request_body,
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise McpRemoteTimeoutError("MCP endpoint timed out; write outcome requires reconciliation") from exc
        except httpx.HTTPError as exc:
            raise McpConnectionDisabledError("MCP endpoint request failed") from exc

        if response.is_redirect:
            raise McpEndpointBlockedError("Redirect responses are forbidden for MCP egress")
        if response.status_code >= 400:
            raise McpConnectionDisabledError(f"MCP endpoint returned HTTP {response.status_code}")
        max_response_bytes = approved_mapping.get("max_response_bytes", 1_048_576)
        if not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise McpEndpointBlockedError("Invalid reviewed MCP response-size limit")
        if len(response.content) > max_response_bytes:
            raise McpEndpointBlockedError("MCP response exceeded the reviewed size limit")
        try:
            payload = response.json()
        except ValueError as exc:
            raise McpConnectionDisabledError("MCP endpoint did not return JSON-RPC JSON") from exc
        if not isinstance(payload, dict):
            raise McpConnectionDisabledError("MCP endpoint returned an invalid JSON-RPC envelope")
        if "error" in payload:
            raise McpConnectionDisabledError("MCP endpoint returned a JSON-RPC error")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise McpConnectionDisabledError("MCP endpoint response is missing a result object")
        return result
