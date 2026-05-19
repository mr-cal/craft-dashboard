"""Tests for the SQLAlchemy base model."""

from craft_dashboard.models.base import Base, TimestampMixin


class TestBase:
    """Tests for the declarative base."""

    def test_base_has_metadata(self) -> None:
        """Base has a metadata attribute."""
        assert Base.metadata is not None

    def test_timestamp_mixin_has_created_at(self) -> None:
        """TimestampMixin defines created_at column."""
        assert "created_at" in TimestampMixin.__dict__

    def test_timestamp_mixin_has_updated_at(self) -> None:
        """TimestampMixin defines updated_at column."""
        assert "updated_at" in TimestampMixin.__dict__
