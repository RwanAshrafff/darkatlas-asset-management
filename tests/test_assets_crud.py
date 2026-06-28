import pytest
from httpx import AsyncClient

TENANT_1_KEY = "darkatlas-tenant1-key-secret"
headers = {"X-API-Key": TENANT_1_KEY}


@pytest.mark.asyncio
async def test_asset_crud_flow(client: AsyncClient):
    """Test standard Asset CRUD cycle."""
    # 1. Create
    asset_data = {
        "type": "domain",
        "value": "crud-test.com",
        "status": "active",
        "source": "manual",
        "tags": ["test"],
        "metadata": {"owner": "sec-ops"}
    }
    response = await client.post("/api/v1/assets", headers=headers, json=asset_data)
    assert response.status_code == 201
    created = response.json()
    assert created["value"] == "crud-test.com"
    assert created["metadata"] == {"owner": "sec-ops"}
    assert "id" in created
    asset_id = created["id"]

    # Try duplicate creation (should fail)
    response_dup = await client.post("/api/v1/assets", headers=headers, json=asset_data)
    assert response_dup.status_code == 400
    assert "already exists" in response_dup.json()["detail"]

    # 2. Read (Single)
    response_get = await client.get(f"/api/v1/assets/{asset_id}", headers=headers)
    assert response_get.status_code == 200
    assert response_get.json()["value"] == "crud-test.com"

    # 3. Update (Merge metadata)
    update_data = {
        "value": "crud-test-updated.com",
        "metadata": {"compliance": "hipaa"}
    }
    response_put = await client.put(f"/api/v1/assets/{asset_id}", headers=headers, json=update_data)
    assert response_put.status_code == 200
    updated = response_put.json()
    assert updated["value"] == "crud-test-updated.com"
    # Metadata should be merged: owner (original) + compliance (new)
    assert updated["metadata"] == {"owner": "sec-ops", "compliance": "hipaa"}

    # 4. Delete
    response_del = await client.delete(f"/api/v1/assets/{asset_id}", headers=headers)
    assert response_del.status_code == 204

    # 5. Read again (404)
    response_get_404 = await client.get(f"/api/v1/assets/{asset_id}", headers=headers)
    assert response_get_404.status_code == 404


@pytest.mark.asyncio
async def test_assets_list_search_and_pagination(client: AsyncClient):
    """Test filtering, sorting, offset, and keyset pagination on list endpoint."""
    # Create multiple assets
    assets_to_create = [
        {"type": "domain", "value": "apple.com", "status": "active", "tags": ["tech", "fruit"]},
        {"type": "domain", "value": "banana.com", "status": "active", "tags": ["fruit"]},
        {"type": "subdomain", "value": "api.banana.com", "status": "stale", "tags": ["tech", "api"]},
        {"type": "ip_address", "value": "1.1.1.1", "status": "archived", "tags": ["cloudflare", "dns"]},
    ]
    
    for item in assets_to_create:
        res = await client.post("/api/v1/assets", headers=headers, json=item)
        assert res.status_code == 201

    # Test filtering by type
    res_type = await client.get("/api/v1/assets?type=domain", headers=headers)
    assert len(res_type.json()["items"]) == 2

    # Test filtering by status
    res_status = await client.get("/api/v1/assets?status=stale", headers=headers)
    assert len(res_status.json()["items"]) == 1
    assert res_status.json()["items"][0]["value"] == "api.banana.com"

    # Test filtering by tag
    res_tag = await client.get("/api/v1/assets?tag=tech", headers=headers)
    assert len(res_tag.json()["items"]) == 2

    # Test partial search
    res_search = await client.get("/api/v1/assets?search=banana", headers=headers)
    assert len(res_search.json()["items"]) == 2

    # Test offset pagination (limit 2)
    res_page1 = await client.get("/api/v1/assets?limit=2&sort_by=value&sort_order=asc", headers=headers)
    page1_data = res_page1.json()
    assert len(page1_data["items"]) == 2
    assert page1_data["items"][0]["value"] == "1.1.1.1"
    assert page1_data["items"][1]["value"] == "api.banana.com"

    res_page2 = await client.get("/api/v1/assets?limit=2&offset=2&sort_by=value&sort_order=asc", headers=headers)
    page2_data = res_page2.json()
    assert len(page2_data["items"]) == 2
    assert page2_data["items"][0]["value"] == "apple.com"
    assert page2_data["items"][1]["value"] == "banana.com"

    # Test keyset pagination (limit 2)
    res_key1 = await client.get("/api/v1/assets?limit=2&pagination_type=keyset&sort_by=last_seen&sort_order=desc", headers=headers)
    key1_data = res_key1.json()
    assert len(key1_data["items"]) == 2
    assert "next_cursor" in key1_data
    cursor = key1_data["next_cursor"]
    assert cursor is not None

    res_key2 = await client.get(f"/api/v1/assets?limit=2&pagination_type=keyset&sort_by=last_seen&sort_order=desc&cursor={cursor}", headers=headers)
    key2_data = res_key2.json()
    # Should get remaining 2 items
    assert len(key2_data["items"]) == 2
