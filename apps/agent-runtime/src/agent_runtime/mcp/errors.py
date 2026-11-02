class McpError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.retryable = retryable

class McpConnectionDisabledError(McpError):
    def __init__(self, message: str = "MCP connection is disabled."):
        super().__init__("MCP_CONNECTION_DISABLED", message, retryable=False)

class McpEndpointBlockedError(McpError):
    def __init__(self, message: str = "MCP endpoint blocked by SSRF egress policy."):
        super().__init__("MCP_ENDPOINT_BLOCKED", message, retryable=False)

class McpToolNotApprovedError(McpError):
    def __init__(self, message: str = "MCP tool mapping is not in 'approved' status."):
        super().__init__("MCP_TOOL_NOT_APPROVED", message, retryable=False)

class McpSchemaDriftError(McpError):
    def __init__(self, message: str = "Remote schema does not match approved schema hash."):
        super().__init__("MCP_SCHEMA_DRIFT", message, retryable=False)

class McpRemoteTimeoutError(McpError):
    def __init__(self, message: str = "MCP remote call timed out."):
        super().__init__("MCP_REMOTE_TIMEOUT", message, retryable=True)

