from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from app.core.config import settings

bearer = HTTPBearer()

# Roles permitted to authenticate to mdm-api. This is an authentication gate
# only — every built-in app role must be able to READ master data (units,
# departments, parts, …). Writes remain restricted per-endpoint via
# require_roles(...). Keep in sync with the built-in roles in epms-api.
ALLOWED_ROLES = {
    "service_account", "system_admin", "finance_manager",
    "gm", "opm", "procurement_manager", "procurement_officer",
    "finance_bp", "ap_clerk", "dept_manager", "dept_admin",
    "requester", "vendor_manager", "warehouse_staff",
    "cfo", "auditor",
}


def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
) -> dict:
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if payload.get("role") not in ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    return payload


CurrentUser = Annotated[dict, Depends(get_token_payload)]


def require_roles(*roles: str):
    """Restrict an endpoint to a fixed set of role codes.

    Usage:
        @router.post(
            "/departments",
            dependencies=[Depends(require_roles("system_admin", "finance_manager", "ap_clerk"))],
        )
    """
    allowed = set(roles)

    def _check(payload: CurrentUser) -> dict:
        if payload.get("role") not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return payload

    return _check
