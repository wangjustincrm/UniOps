from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from app.core.config import settings

bearer = HTTPBearer()


def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return payload


CurrentUser = Annotated[dict, Depends(get_token_payload)]
