class PlatformError(Exception):
    def __init__(self, message: str, code: str = "INTERNAL_ERROR", status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code

class TenantIsolationViolation(PlatformError):
    def __init__(self, message: str = "Access across tenant boundaries is forbidden"):
        super().__init__(message, code="TENANT_VIOLATION", status_code=403)

class StaleUpdateConflictError(PlatformError):
    def __init__(self, message: str = "Optimistic concurrency conflict"):
        super().__init__(message, code="CONFLICT", status_code=409)
