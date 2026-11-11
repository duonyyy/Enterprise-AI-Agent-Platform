from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from control_api.core.auth import AuthenticatedUserContext, get_current_authenticated_user, get_current_principal
from control_api.core.database import get_db_session
from platform_common.principal import AuthenticatedPrincipal

CurrentUser = Annotated[AuthenticatedPrincipal, Depends(get_current_principal)]
CurrentAuthenticatedUser = Annotated[
    AuthenticatedUserContext, Depends(get_current_authenticated_user)
]
DBSession = Annotated[AsyncSession, Depends(get_db_session)]
