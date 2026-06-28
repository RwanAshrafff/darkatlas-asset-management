import logging
from typing import Optional, Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Relationship, Asset
from app.schemas import RelationshipCreate
from app.services.asset_service import get_asset

logger = logging.getLogger("darkatlas.relationship_service")


async def create_relationship(
    db: AsyncSession, tenant_id: UUID, rel_in: RelationshipCreate
) -> Relationship:
    """
    Create a directed link between two assets.
    Enforces tenant isolation by verifying that both assets belong to the current tenant.
    """
    # 1. Verify source and target assets exist and belong to this tenant
    from_asset = await get_asset(db, tenant_id, rel_in.from_asset_id)
    to_asset = await get_asset(db, tenant_id, rel_in.to_asset_id)

    if not from_asset:
        raise ValueError(
            f"Source asset {rel_in.from_asset_id} not found in this tenant."
        )
    if not to_asset:
        raise ValueError(f"Target asset {rel_in.to_asset_id} not found in this tenant.")

    # 2. Insert relationship using ON CONFLICT DO UPDATE for idempotency
    stmt = pg_insert(Relationship).values(
        from_asset_id=rel_in.from_asset_id,
        to_asset_id=rel_in.to_asset_id,
        relationship_type=rel_in.relationship_type,
        is_bidirectional=rel_in.is_bidirectional,
    )

    update_stmt = stmt.on_conflict_do_update(
        index_elements=["from_asset_id", "to_asset_id", "relationship_type"],
        set_={"is_bidirectional": stmt.excluded.is_bidirectional},
    )

    await db.execute(update_stmt)
    await db.commit()

    return Relationship(
        from_asset_id=rel_in.from_asset_id,
        to_asset_id=rel_in.to_asset_id,
        relationship_type=rel_in.relationship_type,
        is_bidirectional=rel_in.is_bidirectional,
    )


async def get_asset_graph(
    db: AsyncSession, tenant_id: UUID, asset_id: UUID
) -> tuple[Optional[Asset], list[dict[str, Any]]]:
    """
    Fetch an asset along with all 1st-degree related neighbors (incoming and outgoing).
    Ensures all fetched neighbors belong to the same tenant.
    """
    # 1. Fetch root asset
    asset = await get_asset(db, tenant_id, asset_id)
    if not asset:
        return None, []

    # 2. Get outgoing relationships (where root asset points to others)
    outgoing_stmt = (
        sa.select(Relationship, Asset)
        .join(Asset, Relationship.to_asset_id == Asset.id)
        .where(Relationship.from_asset_id == asset_id, Asset.tenant_id == tenant_id)
    )

    # 3. Get incoming relationships (where others point to root asset)
    incoming_stmt = (
        sa.select(Relationship, Asset)
        .join(Asset, Relationship.from_asset_id == Asset.id)
        .where(Relationship.to_asset_id == asset_id, Asset.tenant_id == tenant_id)
    )

    outgoing_res = await db.execute(outgoing_stmt)
    incoming_res = await db.execute(incoming_stmt)

    neighbors = []

    # Process outgoing relationships
    for rel, target_asset in outgoing_res.all():
        neighbors.append(
            {
                "asset": target_asset,
                "relationship_type": rel.relationship_type,
                "direction": "outgoing",
            }
        )

    # Process incoming relationships
    for rel, source_asset in incoming_res.all():
        neighbors.append(
            {
                "asset": source_asset,
                "relationship_type": rel.relationship_type,
                "direction": "incoming",
            }
        )

    return asset, neighbors
