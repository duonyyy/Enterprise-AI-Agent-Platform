from fastapi import HTTPException, status

from platform_common.principal import AuthenticatedPrincipal


def require_permission(principal: AuthenticatedPrincipal, permission: str) -> None:
    namespace = permission.split(":", 1)[0]
    if permission not in principal.permissions and f"{namespace}:*" not in principal.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
