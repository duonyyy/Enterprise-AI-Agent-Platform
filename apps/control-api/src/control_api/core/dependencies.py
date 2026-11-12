from typing import Annotated

from fastapi import Depends
from platform_common.principal import AuthenticatedPrincipal
from sqlalchemy.ext.asyncio import AsyncSession

from control_api.core.auth import (
    AuthenticatedUserContext,
    SessionWorkspaceContext,
    get_current_authenticated_user,
    get_current_principal,
    get_current_session_workspace_context,
)
from control_api.core.database import get_db_session

CurrentUser = Annotated[AuthenticatedPrincipal, Depends(get_current_principal)]
CurrentAuthenticatedUser = Annotated[
    AuthenticatedUserContext, Depends(get_current_authenticated_user)
]
CurrentSessionWorkspace = Annotated[
    SessionWorkspaceContext, Depends(get_current_session_workspace_context)
]
DBSession = Annotated[AsyncSession, Depends(get_db_session)]
