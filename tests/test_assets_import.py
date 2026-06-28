from datetime import datetime, timezone, timedelta
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Asset, AssetStatus

TENANT_1_KEY = "darkatlas-tenant1-key-secret"
headers = {"X-API-Key": TENANT_1_KEY}


@pytest.mark.asyncio
async def test_bulk_import_idempotency_and_merges(client: AsyncClient, db_session):
    """Test bulk import deduplication, tag union, metadata merge, and status reversion."""
    
    # 1. First Import
    batch1 = [
        {
            "id": "a1",
            "type": "domain",
            "value": "example.com",
            "status": "active",
            "source": "scan",
            "tags": ["root"],
            "metadata": {"org": "engineering"}
        },
        {
            "id": "a2",
            "type": "subdomain",
            "value": "api.example.com",
            "status": "active",
            "source": "scan",
            "tags": ["prod"],
            "metadata": {"service": "gateway"}
        }
    ]
    response = await client.post("/api/v1/assets/import", headers=headers, json=batch1)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["success_count"] == 2
    assert res_data["error_count"] == 0
    assert len(res_data["errors"]) == 0

    # Verify db count
    stmt = select(Asset)
    res = await db_session.execute(stmt)
    assets = res.scalars().all()
    assert len(assets) == 2

    # Save original times
    a1_orig = next(a for a in assets if a.value == "example.com")
    a1_orig_id = a1_orig.id
    a1_orig_last_seen = a1_orig.last_seen

    # 2. Second Import (Merge tags, overwrite metadata, update last_seen)
    batch2 = [
        {
            "id": "a1",
            "type": "domain",
            "value": "example.com",
            "status": "active",
            "source": "import",
            "tags": ["public", "root"],  # "public" is new, "root" is existing
            "metadata": {"org": "security", "team": "blue"}  # Overwrite "org", add "team"
        }
    ]
    response = await client.post("/api/v1/assets/import", headers=headers, json=batch2)
    assert response.status_code == 200
    assert response.json()["success_count"] == 1

    # Verify db count is still 2 (deduplicated)
    res = await db_session.execute(stmt)
    assets_new = res.scalars().all()
    assert len(assets_new) == 2

    # Retrieve updated a1
    a1_updated = next(a for a in assets_new if a.id == a1_orig_id)
    # Check last_seen is updated (greater or equal)
    assert a1_updated.last_seen >= a1_orig_last_seen
    # Source updated
    assert a1_updated.source == "import"
    # Tags union
    assert set(a1_updated.tags) == {"root", "public"}
    # Metadata merge
    assert a1_updated.metadata_ == {"org": "security", "team": "blue"}


@pytest.mark.asyncio
async def test_bulk_import_resilience_and_stale_reversion(client: AsyncClient, db_session):
    """Test skipping invalid rows, and automatic status reversion for stale assets."""
    
    # 1. Resilience test: Import batch with one valid and one malformed
    batch = [
        {
            "id": "a1",
            "type": "domain",
            "value": "resilient-test.com",
            "status": "active",
            "source": "scan",
            "tags": [],
            "metadata": {}
        },
        {
            "id": "a2",
            "type": "invalid_type",  # Malformed enum
            "value": "bad-enum.com",
        },
        {
            "id": "a3",
            "type": "subdomain",
            "value": "   ",  # Malformed: whitespace value (triggers validator error)
        }
    ]
    response = await client.post("/api/v1/assets/import", headers=headers, json=batch)
    assert response.status_code == 200
    data = response.json()
    
    assert data["success_count"] == 1
    assert data["error_count"] == 2
    assert len(data["errors"]) == 2
    
    # Check that error report lists details for the bad items
    error_indices = [err["index"] for err in data["errors"]]
    assert 1 in error_indices
    assert 2 in error_indices

    # Verify that the valid asset was successfully written to the database
    res = await db_session.execute(select(Asset).where(Asset.value == "resilient-test.com"))
    asset = res.scalar_one_or_none()
    assert asset is not None
    
    # 2. Reversion test: Transition asset manually to stale, then re-import to check automatic active transition
    asset.status = AssetStatus.STALE.value
    db_session.add(asset)
    await db_session.commit()
    
    # Re-import same asset
    re_import_batch = [{
        "type": "domain",
        "value": "resilient-test.com",
        "source": "scan"
    }]
    response = await client.post("/api/v1/assets/import", headers=headers, json=re_import_batch)
    assert response.status_code == 200
    
    # Verify status reverted to active
    await db_session.refresh(asset)
    assert asset.status == AssetStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_stale_lifecycle_cleanup(client: AsyncClient, db_session):
    """Test manual/background cleanup transitioning old assets to stale."""
    # 1. Create a fresh asset
    res_create = await client.post("/api/v1/assets", headers=headers, json={
        "type": "domain",
        "value": "lifecycle.com",
        "source": "manual"
    })
    assert res_create.status_code == 201
    asset_id = res_create.json()["id"]

    # 2. Retrieve from db and modify last_seen to be > 30 days ago
    res_db = await db_session.execute(select(Asset).where(Asset.id == asset_id))
    asset = res_db.scalar_one()
    
    # 31 days ago
    asset.last_seen = datetime.now(timezone.utc) - timedelta(days=31)
    db_session.add(asset)
    await db_session.commit()

    # 3. Trigger cleanup
    res_clean = await client.post("/api/v1/assets/cleanup-stale", headers=headers)
    assert res_clean.status_code == 200
    assert res_clean.json()["transitioned_count"] == 1

    # 4. Check status transitioned to stale
    await db_session.refresh(asset)
    assert asset.status == AssetStatus.STALE.value
