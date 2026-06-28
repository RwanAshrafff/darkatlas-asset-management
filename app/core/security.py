from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings

# Authentication Schemes
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
security_bearer = HTTPBearer(auto_error=False)

# Mock API Key database mapping for multi-tenant isolation testing
MOCK_API_KEYS = {
    "darkatlas-tenant1-key-secret": "11111111-1111-1111-1111-111111111111",
    "darkatlas-tenant2-key-secret": "22222222-2222-2222-2222-222222222222",
}


def create_access_token(
    tenant_id: UUID, expires_delta: Optional[timedelta] = None
) -> str:
    """Create a signed JWT containing the tenant_id claim."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode = {"exp": expire, "tenant_id": str(tenant_id), "iss": "darkatlas"}

    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt


def decode_access_token(token: str) -> Optional[UUID]:
    """Decode a JWT and extract the tenant_id claim if valid."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            issuer="darkatlas",
        )
        tenant_str = payload.get("tenant_id")
        if not tenant_str:
            return None
        return UUID(tenant_str)
    except (jwt.PyJWTError, ValueError):
        return None


async def get_current_tenant_id(
    api_key: Optional[str] = Security(api_key_header),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> UUID:
    """
    Validate the credentials (either JWT Bearer token or API Key) and return the tenant_id UUID.
    Raises 401 Unauthorized if neither credential is valid.
    """
    # 1. Try API Key
    if api_key:
        tenant_str = MOCK_API_KEYS.get(api_key)
        if tenant_str:
            try:
                return UUID(tenant_str)
            except ValueError:
                pass

    # 2. Try JWT Bearer token
    if bearer:
        token = bearer.credentials
        tenant_id = decode_access_token(token)
        if tenant_id:
            return tenant_id

    # 3. Fail if neither is valid
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials. Please provide a valid X-API-Key or Bearer token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
