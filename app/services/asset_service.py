import base64
from datetime import datetime, timezone, timedelta
import logging
from typing import Optional
from uuid import UUID
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert, ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.cache import cache_manager
from app.models import Asset, AssetStatus, Relationship
from app.schemas import (
    AssetCreate, AssetUpdate, BulkImportItem, BulkImportResponse,
    ImportErrorDetail
)

logger = logging.getLogger("darkatlas.asset_service")


# Cursor helper functions for Keyset Pagination
def encode_cursor(last_seen: datetime, asset_id: UUID) -> str:
    """Encode keyset pagination parameters into a base64 cursor string."""
    cursor_str = f"{last_seen.isoformat()}|{asset_id}"
    return base64.b64encode(cursor_str.encode("utf-8")).decode("utf-8")


def decode_cursor(cursor_str: str) -> Optional[tuple[datetime, UUID]]:
    """Decode a base64 cursor string into last_seen datetime and asset_id UUID."""
    try:
        decoded = base64.b64decode(cursor_str.encode("utf-8")).decode("utf-8")
        last_seen_str, asset_id_str = decoded.split("|")
        last_seen = datetime.fromisoformat(last_seen_str)
        asset_id = UUID(asset_id_str)
        return last_seen, asset_id
    except Exception:
        return None


async def get_asset(db: AsyncSession, tenant_id: UUID, asset_id: UUID) -> Optional[Asset]:
    """Retrieve a single asset by ID and scope it to the tenant."""
    query = sa.select(Asset).where(Asset.id == asset_id, Asset.tenant_id == tenant_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_assets(
    db: AsyncSession,
    tenant_id: UUID,
    type: Optional[str] = None,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "last_seen",
    sort_order: str = "desc",
    pagination_type: str = "offset",
    offset: int = 0,
    limit: int = 20,
    cursor: Optional[str] = None,
) -> tuple[list[Asset], Optional[str]]:
    """
    List and search assets.
    Supports filtering, sorting, offset pagination, and keyset pagination.
    Returns (list_of_assets, next_cursor_string).
    """
    # 1. Base Query
    query = sa.select(Asset).where(Asset.tenant_id == tenant_id)

    # 2. Filtering
    if type:
        query = query.where(Asset.type == type)
    if status:
        query = query.where(Asset.status == status)
    if tag:
        # PostgreSQL Array contains operator
        query = query.where(Asset.tags.contains([tag]))
    if search:
        query = query.where(Asset.value.ilike(f"%{search}%"))

    # 3. Sorting and Keyset Pagination constraints
    # Default to last_seen if invalid sort field passed
    sort_col = Asset.last_seen if sort_by == "last_seen" else Asset.first_seen if sort_by == "first_seen" else Asset.value
    order_desc = sort_order.lower() == "desc"
    
    # Deterministic sorting requires tie-breaker (id)
    if order_desc:
        sort_args = [sort_col.desc(), Asset.id.desc()]
    else:
        sort_args = [sort_col.asc(), Asset.id.asc()]

    # 4. Keyset Pagination
    next_cursor = None
    if pagination_type == "keyset":
        if cursor:
            cursor_data = decode_cursor(cursor)
            if cursor_data:
                c_last_seen, c_id = cursor_data
                # Keyset filtering logic
                if order_desc:
                    query = query.where(
                        sa.or_(
                            sort_col < c_last_seen,
                            sa.and_(sort_col == c_last_seen, Asset.id < c_id)
                        )
                    )
                else:
                    query = query.where(
                        sa.or_(
                            sort_col > c_last_seen,
                            sa.and_(sort_col == c_last_seen, Asset.id > c_id)
                        )
                    )
        
        # Apply sort and limit
        query = query.order_by(*sort_args).limit(limit)
        result = await db.execute(query)
        assets = list(result.scalars().all())
        
        # Generate next cursor if we retrieved the full page size
        if len(assets) == limit:
            last_item = assets[-1]
            # Assumes sort_by is last_seen or first_seen (datetimes). If value, we extract value
            cursor_val = getattr(last_item, sort_by) if hasattr(last_item, sort_by) else last_item.last_seen
            if not isinstance(cursor_val, datetime):
                # Fallback to last_seen for cursor generation if sort_by is value
                cursor_val = last_item.last_seen
            next_cursor = encode_cursor(cursor_val, last_item.id)

    else:
        # Offset Pagination
        query = query.order_by(*sort_args).offset(offset).limit(limit)
        result = await db.execute(query)
        assets = list(result.scalars().all())

    return assets, next_cursor


async def create_asset(db: AsyncSession, tenant_id: UUID, asset_in: AssetCreate) -> Asset:
    """Create a new asset, invalidate cache."""
    # Check if duplicate exists within the tenant
    dup_query = sa.select(Asset).where(
        Asset.tenant_id == tenant_id,
        Asset.type == asset_in.type.value,
        Asset.value == asset_in.value
    )
    dup = await db.execute(dup_query)
    if dup.scalar_one_or_none():
        raise ValueError(f"Asset of type '{asset_in.type.value}' with value '{asset_in.value}' already exists.")

    new_asset = Asset(
        tenant_id=tenant_id,
        type=asset_in.type.value,
        value=asset_in.value,
        status=asset_in.status.value,
        source=asset_in.source,
        tags=asset_in.tags,
        metadata_=asset_in.metadata
    )
    db.add(new_asset)
    await db.commit()
    await db.refresh(new_asset)
    
    # Invalidate cache
    await cache_manager.clear_tenant_cache(tenant_id)
    return new_asset


async def update_asset(db: AsyncSession, tenant_id: UUID, asset_id: UUID, asset_in: AssetUpdate) -> Optional[Asset]:
    """Update an existing asset, invalidate cache."""
    asset = await get_asset(db, tenant_id, asset_id)
    if not asset:
        return None
        
    update_data = asset_in.model_dump(exclude_unset=True)
    
    # Merge metadata if provided
    if "metadata" in update_data:
        merged_meta = {**asset.metadata_, **(update_data.pop("metadata") or {})}
        asset.metadata_ = merged_meta

    for field, val in update_data.items():
        if field == "type" or field == "status":
            setattr(asset, field, val.value)
        else:
            setattr(asset, field, val)

    await db.commit()
    await db.refresh(asset)
    
    # Invalidate cache
    await cache_manager.clear_tenant_cache(tenant_id)
    return asset


async def delete_asset(db: AsyncSession, tenant_id: UUID, asset_id: UUID) -> bool:
    """Delete an asset, invalidate cache."""
    asset = await get_asset(db, tenant_id, asset_id)
    if not asset:
        return False
        
    await db.delete(asset)
    await db.commit()
    
    # Invalidate cache
    await cache_manager.clear_tenant_cache(tenant_id)
    return True


async def bulk_import_assets(
    db: AsyncSession,
    tenant_id: UUID,
    raw_items: list[dict]
) -> BulkImportResponse:
    """
    Perform O(N) bulk upsert and relationship linking.
    Invalidates cache upon success.
    Resilient: skips malformed records, collecting errors.
    """
    now_utc = datetime.now(timezone.utc)
    valid_items: list[BulkImportItem] = []
    errors: list[ImportErrorDetail] = []
    warnings: list[str] = []
    
    # 1. Pydantic validation phase (Resilience)
    for idx, raw in enumerate(raw_items):
        try:
            # Map "metadata" in input to "metadata" field in schema (which validates/aliases to metadata_ internally)
            item = BulkImportItem.model_validate(raw)
            valid_items.append(item)
        except Exception as e:
            # Gather errors
            error_msgs = []
            if hasattr(e, "errors"):
                for error in e.errors():
                    loc = " -> ".join(str(loc_val) for loc_val in error.get("loc", []))
                    msg = error.get("msg", "Validation error")
                    error_msgs.append(f"[{loc}]: {msg}")
            else:
                error_msgs.append(str(e))
            
            errors.append(ImportErrorDetail(
                index=idx,
                value=raw.get("value"),
                errors=error_msgs
            ))
            
    if not valid_items:
        return BulkImportResponse(
            success_count=0,
            error_count=len(errors),
            errors=errors,
            warnings=["No valid assets found to import."]
        )
        
    # 2. Database Upsert Phase
    # Compile the values list
    upsert_values = []
    for item in valid_items:
        upsert_values.append({
            "id": uuid.uuid4(),  # pre-generate UUID so we have it if it's a new row
            "tenant_id": tenant_id,
            "type": item.type.value,
            "value": item.value,
            "status": AssetStatus.ACTIVE.value,  # revert to active on import
            "first_seen": now_utc,
            "last_seen": now_utc,
            "source": item.source,
            "tags": item.tags,
            "metadata": item.metadata  # database column is metadata
        })

    # Prepare PostgreSQL bulk upsert statement
    stmt = pg_insert(Asset).values(upsert_values)
    
    # Custom array union query for SQLAlchemy/PostgreSQL ON CONFLICT DO UPDATE
    # Set union of tags: array_cat(coalesce(tags, '{}'), coalesce(excluded.tags, '{}')) select distinct unnested
    tags_union_subquery = sa.func.array(
        sa.select(sa.literal_column("val").distinct())
        .select_from(
            sa.func.unnest(
                sa.func.array_cat(
                    sa.func.coalesce(Asset.tags, sa.cast(sa.func.array([]), ARRAY(sa.String))),
                    sa.func.coalesce(stmt.excluded.tags, sa.cast(sa.func.array([]), ARRAY(sa.String)))
                )
            ).alias("val")
        ).scalar_subquery()
    )

    update_stmt = stmt.on_conflict_do_update(
        constraint="uq_tenant_type_value",
        set_={
            "status": AssetStatus.ACTIVE.value,  # revert to active if stale/archived
            "last_seen": stmt.excluded.last_seen,
            "source": stmt.excluded.source,
            # Merge JSONB metadata using PG concat operator (||)
            "metadata": Asset.metadata_.concat(stmt.excluded.metadata_),
            "tags": tags_union_subquery
        }
    ).returning(Asset.id, Asset.type, Asset.value)

    # Execute bulk upsert
    result = await db.execute(update_stmt)
    returned_rows = result.all()
    
    # Map (type, value) to database UUID
    db_assets_map: dict[tuple[str, str], UUID] = {
        (row.type, row.value): row.id for row in returned_rows
    }
    
    # Map input string ID (e.g. "a1") to database UUID
    import_id_to_uuid: dict[str, UUID] = {}
    for item in valid_items:
        key = (item.type.value, item.value)
        db_uuid = db_assets_map.get(key)
        if db_uuid and item.id:
            import_id_to_uuid[item.id] = db_uuid

    # 3. Parse Relationships Graph Phase
    relationships_to_insert = []
    
    # Define fields we should ignore when looking for custom relationship fields
    # Standard Pydantic model fields (including aliases / attributes)
    standard_fields = {"id", "type", "value", "status", "source", "tags", "metadata"}

    for item in valid_items:
        key = (item.type.value, item.value)
        from_uuid = db_assets_map.get(key)
        if not from_uuid:
            continue
            
        # Pydantic v2 extra fields (relationships)
        extra_fields = item.model_extra or {}
        for rel_type, rel_target in extra_fields.items():
            if rel_type in standard_fields:
                continue
                
            # Target could be a single ID string or a list of ID strings
            targets = [rel_target] if isinstance(rel_target, str) else rel_target if isinstance(rel_target, list) else []
            
            for t_id in targets:
                if not isinstance(t_id, str):
                    continue
                    
                to_uuid = None
                
                # Check batch import ID mapping (e.g. "a1")
                if t_id in import_id_to_uuid:
                    to_uuid = import_id_to_uuid[t_id]
                else:
                    # Check if target is a valid UUID representing an existing asset in DB
                    try:
                        potential_uuid = UUID(t_id)
                        # We will insert it and let database FK validation handle it, or query first.
                        # Querying is safer to return clear warning, but to avoid N+1 we can just assume it exists
                        # if it's a valid UUID, or we can check. Since we want resilience, let's verify.
                        to_uuid = potential_uuid
                    except ValueError:
                        warnings.append(
                            f"Skipped relationship '{item.value}' --[{rel_type}]--> '{t_id}'. Target ID is not found in import batch and is not a valid UUID."
                        )
                        continue
                
                if to_uuid:
                    relationships_to_insert.append({
                        "from_asset_id": from_uuid,
                        "to_asset_id": to_uuid,
                        "relationship_type": rel_type,
                        "is_bidirectional": False
                    })

    # Bulk insert relationships if any
    if relationships_to_insert:
        rel_stmt = pg_insert(Relationship).values(relationships_to_insert)
        # Avoid duplicate relationship insertions
        rel_update_stmt = rel_stmt.on_conflict_do_nothing(
            constraint="uq_from_to_type" if hasattr(Relationship, "__table_args__") else None,
            index_elements=["from_asset_id", "to_asset_id", "relationship_type"]
        )
        try:
            await db.execute(rel_update_stmt)
        except Exception as e:
            # In case of FK failure (e.g., target UUID does not exist in DB)
            logger.warning(f"Batch relationships insert warning: {e}")
            warnings.append("Some relationships were skipped due to referential integrity (target asset does not exist).")

    # Invalidate cache
    await cache_manager.clear_tenant_cache(tenant_id)
    await db.commit()
    
    return BulkImportResponse(
        success_count=len(valid_items),
        error_count=len(errors),
        errors=errors,
        warnings=warnings
    )


async def transition_stale_assets(db: AsyncSession, tenant_id: UUID) -> int:
    """
    Transition assets that have not been seen for a threshold period to 'stale'.
    Returns number of assets modified.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.STALE_THRESHOLD_DAYS)
    
    stmt = sa.update(Asset).where(
        Asset.tenant_id == tenant_id,
        Asset.status == AssetStatus.ACTIVE.value,
        Asset.last_seen < cutoff
    ).values(status=AssetStatus.STALE.value)
    
    result = await db.execute(stmt)
    await db.commit()
    
    count = result.rowcount
    if count > 0:
        await cache_manager.clear_tenant_cache(tenant_id)
        
    return count
