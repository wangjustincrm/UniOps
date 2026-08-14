from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from app.core.config import settings

bearer = HTTPBearer()

# Read access to finance endpoints — NOT payment execution authority (that
# gate is app/crud/payment_execute.py's _PAY_ROLES). ap_clerk stays here: AP
# still needs to read finance data even though (2026-08-13 SoD) it can no
# longer execute a payment. payment_officer is added so it can read what its
# new execute authority needs.
_FINANCE_ROLES = {"system_admin", "finance_manager", "finance_bp", "ap_clerk",
                  "payment_officer", "service_account"}


def get_token_payload(credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return payload


CurrentUser = Annotated[dict, Depends(get_token_payload)]


def get_bearer_token(credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]) -> str:
    return credentials.credentials


BearerToken = Annotated[str, Depends(get_bearer_token)]
