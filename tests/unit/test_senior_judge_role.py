"""Unit tests for senior judge role functionality."""

import pytest

from src.models.user import UserRole


@pytest.mark.asyncio
async def test_senior_judge_role_values():
    """Test that UserRole enum contains all expected values."""
    assert hasattr(UserRole, "judge")
    assert hasattr(UserRole, "admin")
    assert hasattr(UserRole, "senior_judge")

    assert UserRole.judge.value == "judge"
    assert UserRole.admin.value == "admin"
    assert UserRole.senior_judge.value == "senior_judge"
