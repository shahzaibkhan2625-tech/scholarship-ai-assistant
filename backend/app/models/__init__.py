"""Eagerly import every ORM module so SQLAlchemy's declarative registry can
resolve string-based relationship() forward references (e.g.
Scholarship.funding_details -> "FundingDetails") regardless of which model
module a caller imports first."""

from app.models import (  # noqa: F401
    alert,
    application,
    candidate_validation,
    document,
    funding,
    match,
    monitoring,
    profile,
    requirement,
    scholarship,
    source,
    user,
)
