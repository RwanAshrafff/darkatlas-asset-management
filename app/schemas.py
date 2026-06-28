from datetime import datetime
from typing import Optional, Any
from uuid import UUID
from pydantic import BaseModel, Field, AliasChoices, ConfigDict, field_validator

from app.models import AssetType, AssetStatus


class AssetBase(BaseModel):
    type: AssetType
    value: str
    status: AssetStatus = AssetStatus.ACTIVE
    source: str = "import"
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("metadata", "metadata_")
    )

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Value cannot be empty or whitespace.")
        return stripped


class AssetCreate(AssetBase):
    pass


class AssetUpdate(BaseModel):
    type: Optional[AssetType] = None
    value: Optional[str] = None
    status: Optional[AssetStatus] = None
    source: Optional[str] = None
    tags: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError("Value cannot be empty or whitespace.")
            return stripped
        return v


class AssetResponse(AssetBase):
    id: UUID
    tenant_id: UUID
    first_seen: datetime
    last_seen: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# Schema for bulk import items which can contain temporary IDs and relationship fields
class BulkImportItem(AssetBase):
    id: Optional[str] = (
        None  # Temporary ID (e.g., "a1", "a2") used for linking relationships in the batch
    )

    model_config = ConfigDict(
        extra="allow"
    )  # Allow relationship fields like "parent", "covers"


class ImportErrorDetail(BaseModel):
    index: int
    value: Optional[str] = None
    errors: list[str]


class BulkImportResponse(BaseModel):
    success_count: int
    error_count: int
    errors: list[ImportErrorDetail]
    warnings: list[str] = Field(default_factory=list)


# Relationship Schemas
class RelationshipCreate(BaseModel):
    from_asset_id: UUID
    to_asset_id: UUID
    relationship_type: str
    is_bidirectional: bool = False

    @field_validator("relationship_type")
    @classmethod
    def validate_rel_type(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Relationship type cannot be empty.")
        return stripped


class RelationshipResponse(BaseModel):
    from_asset_id: UUID
    to_asset_id: UUID
    relationship_type: str
    is_bidirectional: bool

    model_config = ConfigDict(from_attributes=True)


class NeighborResponse(BaseModel):
    asset: AssetResponse
    relationship_type: str
    direction: str  # "outgoing" or "incoming"


class AssetWithNeighborsResponse(BaseModel):
    asset: AssetResponse
    neighbors: list[NeighborResponse]

    model_config = ConfigDict(from_attributes=True)
