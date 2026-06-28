import hashlib
from typing import Optional, Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.security import get_current_tenant_id
from app.core.cache import cache_manager
from app.schemas import (
    AssetCreate, AssetUpdate, AssetResponse,
    BulkImportResponse
)
from app.services import asset_service

router = APIRouter()


class PaginatedAssetsResponse(BaseModel):
    items: list[AssetResponse]
    next_cursor: Optional[str] = None
    offset: Optional[int] = None
    limit: int


def _generate_list_cache_key(tenant_id: UUID, params: dict[str, Any]) -> str:
    """Generate a deterministic cache key for list asset requests under a tenant namespace."""
    sorted_items = sorted([(k, str(v)) for k, v in params.items() if v is not None])
    query_str = "&".join(f"{k}={v}" for k, v in sorted_items)
    query_hash = hashlib.md5(query_str.encode("utf-8")).hexdigest()
    return f"tenant:{tenant_id}:assets:list:{query_hash}"


@router.get("", response_model=PaginatedAssetsResponse, summary="List and search assets with filtering and pagination")
async def list_assets(
    type: Optional[str] = Query(None, description="Filter by asset type"),
    status: Optional[str] = Query(None, description="Filter by status (active, stale, archived)"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    search: Optional[str] = Query(None, description="Search value field (partial case-insensitive match)"),
    sort_by: str = Query("last_seen", description="Field to sort by (last_seen, first_seen, value)"),
    sort_order: str = Query("desc", description="Sort direction (asc, desc)"),
    pagination_type: str = Query("offset", description="Pagination type (offset or keyset)"),
    offset: int = Query(0, ge=0, description="Offset for offset pagination"),
    limit: int = Query(20, ge=1, le=100, description="Page size limit"),
    cursor: Optional[str] = Query(None, description="Cursor for keyset pagination"),
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Retrieve list of assets scoped to the tenant.
    This endpoint implements caching. Modifications automatically invalidate all tenant-associated cache.
    """
    # 1. Generate cache key based on query parameters
    params = {
        "type": type, "status": status, "tag": tag, "search": search,
        "sort_by": sort_by, "sort_order": sort_order, "pagination_type": pagination_type,
        "offset": offset, "limit": limit, "cursor": cursor
    }
    cache_key = _generate_list_cache_key(tenant_id, params)

    # 2. Try Cache
    cached_data = await cache_manager.get_json(cache_key)
    if cached_data is not None:
        return PaginatedAssetsResponse(**cached_data)

    # 3. Cache Miss - Query DB
    assets, next_cursor = await asset_service.get_assets(
        db=db,
        tenant_id=tenant_id,
        type=type,
        status=status,
        tag=tag,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        pagination_type=pagination_type,
        offset=offset,
        limit=limit,
        cursor=cursor
    )

    # Convert to schema response
    response_items = [AssetResponse.model_validate(asset) for asset in assets]
    result = PaginatedAssetsResponse(
        items=response_items,
        next_cursor=next_cursor,
        offset=offset if pagination_type == "offset" else None,
        limit=limit
    )

    # 4. Save to Cache (expire in 5 minutes)
    await cache_manager.set_json(cache_key, result.model_dump(mode="json"), expire=300)
    
    return result


@router.get("/{id}", response_model=AssetResponse, summary="Retrieve a single asset details")
async def get_asset(
    id: UUID,
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    asset = await asset_service.get_asset(db, tenant_id, id)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset with ID '{id}' not found."
        )
    return asset


@router.post("", response_model=AssetResponse, status_code=status.HTTP_201_CREATED, summary="Create a single asset")
async def create_asset(
    asset_in: AssetCreate,
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    try:
        new_asset = await asset_service.create_asset(db, tenant_id, asset_in)
        return new_asset
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.put("/{id}", response_model=AssetResponse, summary="Update an existing asset details")
async def update_asset(
    id: UUID,
    asset_in: AssetUpdate,
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    updated = await asset_service.update_asset(db, tenant_id, id, asset_in)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset with ID '{id}' not found."
        )
    return updated


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an asset")
async def delete_asset(
    id: UUID,
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    deleted = await asset_service.delete_asset(db, tenant_id, id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset with ID '{id}' not found."
        )
    return None


@router.post("/import", response_model=BulkImportResponse, summary="Bulk import a list of assets and establish relations")
async def import_assets(
    items: list[dict],
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Accepts a list of raw dictionaries containing asset information.
    Resiliently processes validation, upserts assets, resolves and saves relations, and returns metrics.
    """
    res = await asset_service.bulk_import_assets(db, tenant_id, items)
    return res


class CleanupStaleResponse(BaseModel):
    transitioned_count: int


@router.post("/cleanup-stale", response_model=CleanupStaleResponse, summary="Transition inactive assets to stale status")
async def cleanup_stale(
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    count = await asset_service.transition_stale_assets(db, tenant_id)
    return CleanupStaleResponse(transitioned_count=count)
