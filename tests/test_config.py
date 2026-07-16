import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_requires_application_secret() -> None:
    with pytest.raises(ValidationError, match="APP_SECRET_KEY"):
        Settings(app_env="production")


def test_development_allows_no_integration_credentials() -> None:
    settings = Settings(app_env="development")

    assert settings.alegra_api_basic_token is None
