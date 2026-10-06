"""Legacy mocked API fixtures explicitly opt out of the production family lock."""
import pytest


@pytest.fixture(autouse=True)
def legacy_api_model_policy(request, monkeypatch):
    # Only the established mocked server fixture needs arbitrary fake models.
    # Startup-policy tests have no server fixture and test the real default in
    # isolated processes; do not install a global environment default here.
    if 'server' in request.fixturenames:
        monkeypatch.setenv('H3_STUDIO_MODEL_POLICY', '')
