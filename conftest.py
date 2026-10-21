"""Tests see the environment CI sees. A developer's .env (VS Code loads it into test runs) holds the production
database URL and live API keys; no test may reach either, and a test that passes in CI must not change behaviour
because a key happens to be set."""
import os

_LIVE = {"OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD",
         "PAGESPEED_API_KEY", "AZURE_KEY_VAULT_URL", "DEPLOY_WEBHOOK_SECRET", "VERCEL_WEBHOOK_SECRET"}

for _key in [k for k in os.environ if k in _LIVE or (k.startswith("SEO_") and k != "SEO_TEST_DATABASE_URL")]:
    del os.environ[_key]
