import json
from pathlib import Path
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Asset

TENANT_1_KEY = "darkatlas-tenant1-key-secret"
TENANT_2_KEY = "darkatlas-tenant2-key-secret"
headers = {"X-API-Key": TENANT_1_KEY}


@pytest.mark.asyncio
async def test_manual_relationship_and_graph(client: AsyncClient):
    """Test manual relationship creation and retrieving neighbors."""
    # 1. Create two assets
    res_a = await client.post("/api/v1/assets", headers=headers, json={
        "type": "domain", "value": "parent-domain.com"
    })
    res_b = await client.post("/api/v1/assets", headers=headers, json={
        "type": "subdomain", "value": "sub.parent-domain.com"
    })
    assert res_a.status_code == 201
    assert res_b.status_code == 201
    
    a_id = res_a.json()["id"]
    b_id = res_b.json()["id"]

    # 2. Link subdomain to parent domain
    rel_data = {
        "from_asset_id": b_id,
        "to_asset_id": a_id,
        "relationship_type": "parent",
        "is_bidirectional": False
    }
    res_rel = await client.post("/api/v1/relationships", headers=headers, json=rel_data)
    assert res_rel.status_code == 201
    
    # 3. Fetch graph of B (subdomain - should have OUTGOING relation to parent)
    res_graph_b = await client.get(f"/api/v1/assets/{b_id}/graph", headers=headers)
    assert res_graph_b.status_code == 200
    graph_b = res_graph_b.json()
    assert graph_b["asset"]["id"] == b_id
    assert len(graph_b["neighbors"]) == 1
    assert graph_b["neighbors"][0]["asset"]["id"] == a_id
    assert graph_b["neighbors"][0]["relationship_type"] == "parent"
    assert graph_b["neighbors"][0]["direction"] == "outgoing"

    # 4. Fetch graph of A (parent domain - should have INCOMING relation from subdomain)
    res_graph_a = await client.get(f"/api/v1/assets/{a_id}/graph", headers=headers)
    assert res_graph_a.status_code == 200
    graph_a = res_graph_a.json()
    assert graph_a["asset"]["id"] == a_id
    assert len(graph_a["neighbors"]) == 1
    assert graph_a["neighbors"][0]["asset"]["id"] == b_id
    assert graph_a["neighbors"][0]["relationship_type"] == "parent"
    assert graph_a["neighbors"][0]["direction"] == "incoming"


@pytest.mark.asyncio
async def test_relationship_tenant_isolation(client: AsyncClient):
    """Test that links cannot be created between assets in different tenants."""
    # 1. Create asset in Tenant 1
    res_t1 = await client.post("/api/v1/assets", headers={"X-API-Key": TENANT_1_KEY}, json={
        "type": "domain", "value": "tenant1-domain.com"
    })
    # 2. Create asset in Tenant 2
    res_t2 = await client.post("/api/v1/assets", headers={"X-API-Key": TENANT_2_KEY}, json={
        "type": "domain", "value": "tenant2-domain.com"
    })
    assert res_t1.status_code == 201
    assert res_t2.status_code == 201
    
    id_t1 = res_t1.json()["id"]
    id_t2 = res_t2.json()["id"]

    # 3. Attempt to link them under Tenant 1 (should fail)
    rel_data = {
        "from_asset_id": id_t1,
        "to_asset_id": id_t2,
        "relationship_type": "cross_link"
    }
    res_link = await client.post("/api/v1/relationships", headers={"X-API-Key": TENANT_1_KEY}, json=rel_data)
    assert res_link.status_code == 400
    assert "not found" in res_link.json()["detail"].lower()


@pytest.mark.asyncio
async def test_import_integration_with_provided_json(client: AsyncClient, db_session):
    """
    Integration test using the exact Data.json provided in the workspace.
    Verifies that the import successfully creates assets, maps relations,
    and returns correct neighbors in the graph.
    """
    # 1. Read Data.json from the workspace
    data_json_path = Path(__file__).resolve().parents[1] / "Data.json"
    with data_json_path.open("r", encoding="utf-8") as f:
        import_data = json.load(f)

    # 2. POST to import endpoint
    response = await client.post("/api/v1/assets/import", headers=headers, json=import_data)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["success_count"] == 3
    assert res_data["error_count"] == 0

    # 3. Retrieve assets to find generated UUIDs
    assets_res = await db_session.execute(select(Asset))
    assets = assets_res.scalars().all()
    assert len(assets) == 3

    # Map values to assets
    domain_asset = next(a for a in assets if a.value == "example.com")
    subdomain_asset = next(a for a in assets if a.value == "api.example.com")
    cert_asset = next(a for a in assets if a.value == "CN=api.example.com")

    # 4. Fetch the graph of the subdomain to verify connections
    res_graph = await client.get(f"/api/v1/assets/{subdomain_asset.id}/graph", headers=headers)
    assert res_graph.status_code == 200
    graph_data = res_graph.json()
    
    # Subdomain should have 2 neighbors:
    # - parent (outgoing) -> example.com (domain)
    # - covers (incoming) <- CN=api.example.com (cert)
    neighbors = graph_data["neighbors"]
    assert len(neighbors) == 2
    
    # Verify parent (outgoing) link
    parent_link = next(n for n in neighbors if n["relationship_type"] == "parent")
    assert parent_link["asset"]["id"] == str(domain_asset.id)
    assert parent_link["direction"] == "outgoing"
    
    # Verify covers (incoming) link
    covers_link = next(n for n in neighbors if n["relationship_type"] == "covers")
    assert covers_link["asset"]["id"] == str(cert_asset.id)
    assert covers_link["direction"] == "incoming"
