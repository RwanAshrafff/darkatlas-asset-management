from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.security import create_access_token

router = APIRouter()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tenant_id: UUID


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Generate a JWT token for a specific tenant",
)
async def get_token(
    tenant_id: Optional[UUID] = Query(
        None,
        description="Tenant UUID. Defaults to a standard mock tenant 1: 11111111-1111-1111-1111-111111111111",
    )
):
    """
    Generate a signed JWT token containing a tenant_id claim.
    Useful for testing API calls with tenant-scoped Bearer tokens.
    """
    if not tenant_id:
        tenant_id = UUID("11111111-1111-1111-1111-111111111111")

    access_token = create_access_token(tenant_id)
    return TokenResponse(access_token=access_token, tenant_id=tenant_id)
