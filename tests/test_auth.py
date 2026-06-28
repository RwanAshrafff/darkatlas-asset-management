import pytest
from httpx import AsyncClient

# Test Headers
TENANT_1_KEY = "darkatlas-tenant1-key-secret"
TENANT_2_KEY = "darkatlas-tenant2-key-secret"


@pytest.mark.asyncio
async def test_token_generation(client: AsyncClient):
    """Test generating a JWT token for a specific tenant."""
    # 1. Without tenant ID (should use default tenant 1)
    response = await client.post("/api/v1/auth/token")
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["tenant_id"] == "11111111-1111-1111-1111-111111111111"

    # 2. With specific tenant ID
    custom_tenant = "33333333-3333-3333-3333-333333333333"
    response = await client.post(f"/api/v1/auth/token?tenant_id={custom_tenant}")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == custom_tenant


@pytest.mark.asyncio
async def test_authentication_required(client: AsyncClient):
    """Test that requests without valid credentials fail with 401."""
    # Try reading assets without auth
    response = await client.get("/api/v1/assets")
    assert response.status_code == 401
    
    # Try creating asset without auth
    response = await client.post("/api/v1/assets", json={
        "type": "domain",
        "value": "example.com"
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_api_key_authentication(client: AsyncClient):
    """Test auth using the X-API-Key header."""
    headers = {"X-API-Key": TENANT_1_KEY}
    response = await client.get("/api/v1/assets", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 0


@pytest.mark.asyncio
async def test_tenant_isolation(client: AsyncClient):
    """Verify that Tenant 2 cannot access or read assets belonging to Tenant 1."""
    # 1. Create an asset under Tenant 1 using its API Key
    headers_t1 = {"X-API-Key": TENANT_1_KEY}
    create_response = await client.post("/api/v1/assets", headers=headers_t1, json={
        "type": "domain",
        "value": "tenant1-secret.com",
        "source": "manual",
        "tags": ["private"]
    })
    assert create_response.status_code == 201
    asset_id = create_response.json()["id"]

    # 2. Query list under Tenant 2
    headers_t2 = {"X-API-Key": TENANT_2_KEY}
    list_response = await client.get("/api/v1/assets", headers=headers_t2)
    assert list_response.status_code == 200
    list_data = list_response.json()
    assert len(list_data["items"]) == 0  # Cannot see Tenant 1 asset

    # 3. Attempt to fetch specific ID under Tenant 2
    get_response = await client.get(f"/api/v1/assets/{asset_id}", headers=headers_t2)
    assert get_response.status_code == 404  # Returns 404 to avoid leaking existence of the asset
    
    # 4. Success reading it under Tenant 1
    get_response_t1 = await client.get(f"/api/v1/assets/{asset_id}", headers=headers_t1)
    assert get_response_t1.status_code == 200
    assert get_response_t1.json()["value"] == "tenant1-secret.com"
