import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.data.repositories.db import Base


class FundingDetails(Base):
    __tablename__ = "funding_details"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scholarship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scholarships.id"), nullable=False, unique=True, index=True
    )
    tuition_coverage: Mapped[str | None] = mapped_column(String, nullable=True)
    tuition_amount_or_pct: Mapped[str | None] = mapped_column(String, nullable=True)
    stipend: Mapped[str | None] = mapped_column(String, nullable=True)
    accommodation: Mapped[str | None] = mapped_column(String, nullable=True)
    travel_airfare: Mapped[str | None] = mapped_column(String, nullable=True)
    health_insurance: Mapped[str | None] = mapped_column(String, nullable=True)
    visa_support: Mapped[str | None] = mapped_column(String, nullable=True)
    family_dependent_benefits: Mapped[str | None] = mapped_column(String, nullable=True)
    spouse_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dependent_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_status: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[str] = mapped_column(String, nullable=False)

    scholarship: Mapped["Scholarship"] = relationship(back_populates="funding_details")  # noqa: F821
