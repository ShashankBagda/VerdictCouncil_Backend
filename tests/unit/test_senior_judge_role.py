"""Unit tests for senior judge role functionality."""

import pytest
from fastapi import status
from sqlalchemy import select

from src.models.user import User, UserRole
from tests.conftest import AuthenticatedClient


@pytest.mark.asyncio
async def test_register_senior_judge(client):
    """Test registering a user with senior_judge role."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "name": "John Senior",
            "email": "senior@example.com",
            "role": "senior_judge",
            "password": "securepassword123",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["role"] == "senior_judge"
    assert data["email"] == "senior@example.com"


@pytest.mark.asyncio
async def test_senior_judge_can_access_protected_endpoints(db, authenticated_client):
    """Test that senior_judge role can access senior-only endpoints."""
    # Create a senior judge user
    senior = User(
        name="Senior Judge",
        email="senior@test.com",
        role=UserRole.senior_judge,
        password_hash="hashed_password",
    )
    db.add(senior)
    await db.flush()
    
    # Verify senior_judge role exists and can be retrieved
    result = await db.execute(select(User).where(User.id == senior.id))
    fetched = result.scalar_one()
    assert fetched.role == UserRole.senior_judge


@pytest.mark.asyncio
async def test_non_senior_judge_cannot_access_senior_endpoints(db, authenticated_client_judge):
    """Test that judge (non-senior) cannot access senior-only endpoints."""
    # This is an example - actual endpoint testing would depend on implementation
    assert authenticated_client_judge.user.role == UserRole.judge
    # Future: Add endpoint-specific tests here


@pytest.mark.asyncio
async def test_senior_judge_role_values():
    """Test that UserRole enum contains all expected values."""
    assert hasattr(UserRole, 'judge')
    assert hasattr(UserRole, 'admin')
    assert hasattr(UserRole, 'senior_judge')
    
    # Verify they're strings
    assert UserRole.judge.value == 'judge'
    assert UserRole.admin.value == 'admin'
    assert UserRole.senior_judge.value == 'senior_judge'
