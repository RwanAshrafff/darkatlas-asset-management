from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.security import get_current_tenant_id
from app.schemas import (
    RelationshipCreate,
    RelationshipResponse,
    AssetWithNeighborsResponse,
)
from app.services import relationship_service

router = APIRouter()


@router.post(
    "/relationships",
    response_model=RelationshipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a directed connection between two assets",
)
async def create_relationship(
    rel_in: RelationshipCreate,
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Manually link two assets. Both assets must belong to the caller's tenant.
    """
    try:
        new_rel = await relationship_service.create_relationship(
            db=db, tenant_id=tenant_id, rel_in=rel_in
        )
        return new_rel
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get(
    "/assets/{id}/graph",
    response_model=AssetWithNeighborsResponse,
    summary="Fetch an asset and its 1st-degree related neighbors",
)
async def get_asset_graph(
    id: UUID,
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Retrieve an asset details along with all 1st-degree outgoing and incoming neighbors.
    Strictly scoped to the tenant.
    """
    asset, neighbors = await relationship_service.get_asset_graph(
        db=db, tenant_id=tenant_id, asset_id=id
    )

    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset with ID '{id}' not found.",
        )

    return {"asset": asset, "neighbors": neighbors}
