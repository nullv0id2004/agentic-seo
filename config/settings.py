"""Process-level settings. Read from the environment once. No project-specific values live here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    secret_backend: str                    # 'keyvault' | 'env'
    key_vault_url: str | None
    anthropic_api_key: str | None
    analyst_model: str
    verifier_model: str
    pagespeed_api_key: str | None
    dataforseo_login: str | None
    dataforseo_password: str | None
    # Global rate limits per external API, requests per minute. Quotas are per key, not per site.
    rate_limits_per_minute: dict[str, int] = field(default_factory=lambda: {
        "gsc": 600, "gsc_inspection": 60, "ga4": 60, "psi": 25, "dataforseo": 60,
        "http_fetch": 120, "site_crawl": 120, "header_probe": 240, "search_status": 10,
    })
    gsc_inspection_daily_cap: int = 2000
    pitch_batch_cap: int = 50              # Section 6.5, enforced in code
    llm_max_retries: int = 3
    # Price table for cost accounting (USD per million tokens). Update when models change.
    model_prices: dict[str, tuple[float, float]] = field(default_factory=lambda: {
        "claude-sonnet-5": (3.0, 15.0),
        "claude-opus-5": (15.0, 75.0),
        "claude-haiku-4-5-20251001": (1.0, 5.0),
    })

    def price_for(self, model: str) -> tuple[float, float]:
        return self.model_prices.get(model, (15.0, 75.0))  # unknown model: assume the dearest, fail closed


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("SEO_DATABASE_URL"),
        secret_backend=os.environ.get("SEO_SECRET_BACKEND", "keyvault"),
        key_vault_url=os.environ.get("AZURE_KEY_VAULT_URL"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        analyst_model=os.environ.get("SEO_ANALYST_MODEL", "claude-sonnet-5"),
        verifier_model=os.environ.get("SEO_VERIFIER_MODEL", "claude-sonnet-5"),
        pagespeed_api_key=os.environ.get("PAGESPEED_API_KEY"),
        dataforseo_login=os.environ.get("DATAFORSEO_LOGIN"),
        dataforseo_password=os.environ.get("DATAFORSEO_PASSWORD"),
    )
