"""Eagerly import every ORM module so SQLAlchemy's declarative registry can
resolve string-based relationship() forward references (e.g.
Scholarship.funding_details -> "FundingDetails") regardless of which model
module a caller imports first."""

from app.models import funding, match, profile, requirement, scholarship, user  # noqa: F401
