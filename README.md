# DarkAtlas Asset Management API

A production-ready, highly efficient REST API built with FastAPI, PostgreSQL (async via SQLAlchemy & `asyncpg`), and Redis for the Asset Management System module of the **DarkAtlas** Attack Surface Monitoring (ASM) platform.

---

## Technical Stack & Features

- **FastAPI**: Modern, fast (high-performance) web framework for building APIs with Python. Fully typed with Pydantic v2.
- **SQLAlchemy 2.0 (Async)**: Python SQL toolkit and ORM with full async support via `asyncpg`.
- **PostgreSQL 15**: Advanced relational database storing assets and relationships.
- **Lightweight Caching**: Redis-backed cache for list/search operations, with a graceful in-memory dictionary-based fallback if Redis is offline (e.g. during test runs or standalone execution).
- **Multi-Tenant Scoping**: All API routes require credentials (JWT Bearer token or custom `X-API-Key` header) to authenticate and scope operations to a specific `tenant_id`, guaranteeing database isolation.
- **Bulk Import with O(N) Efficiency**: Custom PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` expression that does:
  - Smart metadata dictionary merging (using the PostgreSQL `||` operator).
  - Smart tag list merging (doing a set union of tags using PostgreSQL array manipulation).
  - Resilient row-by-row Pydantic validation: skips malformed records and returns index-specific error messages while successfully importing valid ones.
- **Automated Graph Building**: Relationships (like `"parent"`, `"covers"`) included in the import payload (using temporary string IDs like `"a1"`, `"a2"`) are parsed in the batch and saved as relationships.
- **Keyset & Offset Pagination**: API list endpoints support standard Offset-limit pagination and high-performance Keyset (cursor-based) pagination.

---

## Project Structure

```
d:/NU/Assessment/
│
├── app/
│   ├── api/
│   │   ├── auth.py          # Tenant JWT token generation
│   │   ├── assets.py        # Asset CRUD, list, and bulk import endpoints
│   │   └── relationships.py # Manual linking and 1st-degree neighborhood graph query
│   │
│   ├── core/
│   │   ├── cache.py         # App Cache Manager (Redis + InMemory fallback)
│   │   ├── config.py        # Settings loader with Pydantic BaseSettings
│   │   ├── database.py      # SQLAlchemy async engine, sessionmaker, and Base model
│   │   └── security.py      # Token parsing, API Key mapping, and security dependency
│   │
│   ├── models.py            # Database tables (Asset, Relationship)
│   ├── schemas.py           # Request / Response validation schemas
│   └── main.py              # Application entrypoint & table auto-creation
│
├── tests/
│   ├── conftest.py          # DB engine, async transaction rollback, and httpx client fixtures
│   ├── test_auth.py         # JWT, API Key, and tenant isolation tests
│   ├── test_assets_crud.py  # Single asset CRUD & query filters/pagination tests
│   ├── test_assets_import.py# Bulk import, tag-merging, metadata-merging & stale lifecycle tests
│   └── test_relationships.py# Graph neighborhood, cross-tenant rejection, & Data.json integration tests
│
├── Dockerfile               # Multi-stage production build
├── docker-compose.yml       # API, Postgres, and Redis service configuration
├── requirements.txt         # Production and testing python package dependencies
├── .env.example             # Template for environment variables
└── README.md                # System documentation
```

---

## Environment Variables

| Variable | Description | Default Value |
| :--- | :--- | :--- |
| `POSTGRES_ASYNC_URI` | SQLAlchemy async connection URI. | `postgresql+asyncpg://postgres:postgres@localhost:5432/darkatlas` |
| `REDIS_URI` | Redis connection URL. | `redis://localhost:6379/0` |
| `JWT_SECRET` | Secret key used for signing JWTs. | `super-secret-jwt-key-for-darkatlas-asm-platform-2026` |
| `STALE_THRESHOLD_DAYS` | Cutoff period of inactivity before assets turn `stale`. | `30` |

---

## Running with Docker Compose

Running the entire stack (FastAPI web server, PostgreSQL, and Redis cache) is fully containerized and requires only one command:

```bash
docker-compose up --build
```

- The API will be available at **`http://localhost:8000`**.
- Visual Swagger documentation will be available at **`http://localhost:8000/docs`**.
- Database tables are automatically initialized and created when the API container starts.

---

## Running Locally

To run the server locally outside of Docker (for development, debugging, or running tests):

### 1. Pre-requisites
Make sure you have Python 3.11+ installed.
You need a running PostgreSQL database. You can start the database and cache container in the background:
```bash
docker-compose up -d db cache
```

### 2. Set Up Virtual Environment & Dependencies
```bash
python -m venv venv
venv\Scripts\activate      # On Windows
pip install -r requirements.txt
```

### 3. Run Development Server
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
The API is now running locally at **`http://127.0.0.1:8000`**.

---

## Running the Automated Test Suite

A comprehensive test suite covering authentication, tenant isolation, CRUD, pagination, bulk import (deduplication, merging, resilience), and graph relationships is located in `/tests`.

To run the tests:
1. Ensure the PostgreSQL database container is running (the test suite creates and operates on a test database inside transaction rollbacks):
   ```bash
   docker-compose up -d db
   ```
2. Run `pytest`:
   ```bash
   pytest
   ```

The CI workflow also runs `ruff check .` and `black --check .` on every push and pull request.

All test cases are written using `pytest-asyncio` and are executed concurrently.

---

## Assumptions & Design Choices

1. **Asset Uniqueness Constraint**: An asset in DarkAtlas is uniquely identified by the combination of its `tenant_id`, `type`, and `value`. This composite key is enforced via a unique constraint (`uq_tenant_type_value`) in the PostgreSQL schema.
2. **Bulk Import Graph Parsing**: The import payload uses arbitrary string identifiers (like `"a1"`, `"a2"`) for the input objects, and maps relationships using fields like `"parent": "a1"` or `"covers": "a2"`. Our importer automatically identifies any extra fields in the input that are not standard Asset properties. If a field's value matches an asset ID in the import batch, it establishes a directed relationship using the key name (e.g. `"parent"`) as the relationship type. If the target is not present in the batch, it checks if it is a valid UUID of an existing asset in the database. If not, it skips the relationship and returns a warning.
3. **Set Union of Tags**: During bulk upserts, existing and new tags are merged into a unique list. This is implemented database-side in the `ON CONFLICT` update statement using PostgreSQL's array unnesting and array concatenation:
   ```sql
   ARRAY(SELECT DISTINCT unnest(array_cat(coalesce(assets.tags, '{}'), coalesce(excluded.tags, '{}'))))
   ```
4. **Metadata Merging**: Overwriting conflicting keys during upsert is handled database-side in PostgreSQL using the JSONB concatenation operator `assets.metadata || excluded.metadata`.
5. **Multi-tenant Authentication**:
   - For writing operations (`POST`, `PUT`, `PATCH`, `DELETE`) and read operations (`GET`), the user must provide credentials.
   - The user can supply an `X-API-Key` header mapped to a specific tenant ID:
     - `darkatlas-tenant1-key-secret` -> scopes actions to Tenant `11111111-1111-1111-1111-111111111111`.
     - `darkatlas-tenant2-key-secret` -> scopes actions to Tenant `22222222-2222-2222-2222-222222222222`.
   - Alternatively, the user can call `/api/v1/auth/token?tenant_id=...` to generate a JWT token and provide it in the `Authorization: Bearer <token>` header.
6. **Automatic Status Reversion**: When an asset is re-imported, its status is automatically forced back to `active` (e.g., if it was previously marked as `stale` or `archived`), and its `last_seen` timestamp is updated to the current time.
7. **Stale Asset Lifespan**: A cleanup endpoint `/api/v1/assets/cleanup-stale` transitions assets to `stale` if their `last_seen` timestamp is older than `STALE_THRESHOLD_DAYS` (default 30). This runs a single update query in the database.
