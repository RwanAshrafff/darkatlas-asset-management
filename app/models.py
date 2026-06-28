import enum
from datetime import datetime, timezone
import uuid
from sqlalchemy import String, DateTime, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssetType(str, enum.Enum):
    DOMAIN = "domain"
    SUBDOMAIN = "subdomain"
    IP_ADDRESS = "ip_address"
    SERVICE = "service"
    CERTIFICATE = "certificate"
    TECHNOLOGY = "technology"


class AssetStatus(str, enum.Enum):
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"


class Asset(Base):
    __tablename__ = "assets"
    
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True
    )
    type: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )  # AssetType enum as string
    value: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        index=True
    )  # Canonical value
    status: Mapped[str] = mapped_column(
        String(50),
        default=AssetStatus.ACTIVE.value,
        nullable=False
    )  # AssetStatus enum as string
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(100),
        default="import",
        nullable=False
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        nullable=False
    )
    # Use metadata_ to avoid shadowing SQLAlchemy Base.metadata, map it to PG column "metadata"
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "type", "value", name="uq_tenant_type_value"),
    )


class Relationship(Base):
    __tablename__ = "relationships"
    
    from_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        primary_key=True
    )
    to_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        primary_key=True
    )
    relationship_type: Mapped[str] = mapped_column(
        String(100),
        primary_key=True
    )  # e.g., 'parent', 'covers', 'resolves_to'
    is_bidirectional: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )
