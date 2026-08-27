"""Loads Azure OpenAI settings from the environment.

This is the only place in the project that reads `os.environ`. Everything else
receives a `Settings` object, which keeps the code testable: a test can build a
`Settings` by hand instead of mutating global environment state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# The repo root — the directory that contains `.env`, `app/` and `web/`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# `load_dotenv` never overwrites variables that are already exported in the
# shell, so real CI/production secrets always win over the local `.env` file.
load_dotenv(PROJECT_ROOT / ".env")

#: Environment variable names, kept in one place so error messages can quote them.
ENV_ENDPOINT = "LLM_ENDPOINT_MINI_MODEL"
ENV_API_KEY = "LLM_ENDPOINT_MINI_MODEL_APIKEY"
ENV_DEPLOYMENT = "MINI_MODEL_NAME"

#: SQL Server Express over Windows authentication -- no password to leak, and
#: it is what this project is developed against. Swap the whole URL for
#: "postgresql+psycopg2://user:pass@localhost/bitesbytes" and nothing else in
#: the codebase changes.
DEFAULT_DATABASE_URL = (
    "mssql+pyodbc://@localhost:1433/BitesBytes"
    "?driver=ODBC+Driver+18+for+SQL+Server"
    "&Trusted_Connection=yes"
    "&TrustServerCertificate=yes"
)


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back if unset or malformed."""
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back if unset or malformed."""
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of everything the app needs to talk to Azure OpenAI."""

    endpoint: str
    api_key: str
    deployment: str
    api_version: str
    temperature: float
    max_tool_iterations: int
    port: int
    database_url: str
    session_days: int

    @property
    def missing(self) -> list[str]:
        """Names of the required variables that are still empty or placeholders."""
        required = {
            ENV_ENDPOINT: self.endpoint,
            ENV_API_KEY: self.api_key,
            ENV_DEPLOYMENT: self.deployment,
        }
        return [
            name
            for name, value in required.items()
            # "paste-your-key-here" / "your-resource-name" are the .env.example stubs.
            if not value or "your-" in value or "paste-" in value
        ]

    @property
    def is_configured(self) -> bool:
        """True when the app can actually reach Azure OpenAI."""
        return not self.missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings (parsed once, then cached)."""
    return Settings(
        endpoint=os.getenv(ENV_ENDPOINT, "").strip(),
        api_key=os.getenv(ENV_API_KEY, "").strip(),
        deployment=os.getenv(ENV_DEPLOYMENT, "").strip(),
        api_version=os.getenv("LLM_API_VERSION", "2024-10-21").strip(),
        temperature=_env_float("LLM_TEMPERATURE", 0.2),
        max_tool_iterations=_env_int("MAX_TOOL_ITERATIONS", 6),
        port=_env_int("APP_PORT", 8000),
        database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL).strip(),
        session_days=_env_int("SESSION_DAYS", 14),
    )
