from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from control_api.core.auth import get_current_principal
from control_api.core.database import get_db_session
from platform_common.principal import AuthenticatedPrincipal

CurrentUser = Annotated[AuthenticatedPrincipal, Depends(get_current_principal)]
DBSession = Annotated[AsyncSession, Depends(get_db_session)]
