"""Unit tests for src.api.routes.domains and src.api.schemas.domains.

Verifies:
- Every route on the domains router declares a response_model (no accidental leaks)
- PublicDomainResponse does not expose sensitive fields (vector_store_id, is_active)
"""

from src.api.routes import domains


def test_all_routes_have_response_model():
    """Every route registered on the domains router must declare a response_model."""
    for route in domains.router.routes:
        assert getattr(route, "response_model", None) is not None, (
            f"Route {route.path} is missing response_model — this may expose unintended fields to clients"
        )


def test_public_domain_response_no_sensitive_fields():
    """PublicDomainResponse must not expose vector_store_id or is_active to judges."""
    from src.api.schemas.domains import PublicDomainResponse

    assert "vector_store_id" not in PublicDomainResponse.model_fields, (
        "vector_store_id must be absent from PublicDomainResponse to prevent schema leak"
    )
    assert "is_active" not in PublicDomainResponse.model_fields, (
        "is_active must be absent from PublicDomainResponse to prevent schema leak"
    )


def test_public_domain_response_has_expected_public_fields():
    """PublicDomainResponse exposes exactly the fields needed for the intake dropdown."""
    from src.api.schemas.domains import PublicDomainResponse

    fields = PublicDomainResponse.model_fields
    assert "id" in fields
    assert "code" in fields
    assert "name" in fields


def test_admin_domain_response_has_sensitive_fields():
    """AdminDomainResponse (admin surfaces only) does include vector_store_id and is_active."""
    from src.api.schemas.domains import AdminDomainResponse

    assert "vector_store_id" in AdminDomainResponse.model_fields
    assert "is_active" in AdminDomainResponse.model_fields


def test_admin_domain_response_is_superset_of_public():
    """AdminDomainResponse inherits from PublicDomainResponse — all public fields are present."""
    from src.api.schemas.domains import AdminDomainResponse, PublicDomainResponse

    assert issubclass(AdminDomainResponse, PublicDomainResponse)
