"""Service-account tokens for Search Console and GA4. The JSON key comes from Key Vault via credentials_ref."""
from __future__ import annotations

import json

from google.auth.transport.requests import Request
from google.oauth2 import service_account

from config.secrets import resolve_secret

GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


def access_token(credentials_ref: str | None, scope: str) -> str:
    """Mint a short-lived bearer token. Raises SecretUnavailable when the reference cannot be resolved."""
    info = json.loads(resolve_secret(credentials_ref))
    creds = service_account.Credentials.from_service_account_info(info, scopes=[scope])
    creds.refresh(Request())
    return creds.token
