"""Tokens for Search Console and GA4. Two credential shapes, both resolved from Key Vault by credentials_ref:

  {"type": "service_account", ...}     a JSON key (only where the org policy allows key creation)
  {"type": "external_account", ...}    Workload Identity Federation: the worker's Azure managed identity
                                        is exchanged for a short-lived Google token; nothing long-lived exists

For external_account on Azure App Service the credential_source is filled in at runtime from the
IDENTITY_ENDPOINT / IDENTITY_HEADER variables the platform injects, so the stored config carries no
environment-specific values. On an Azure VM the IMDS endpoint is used instead.
"""
from __future__ import annotations

import json
import os
from typing import Any

import google.auth
from google.auth.transport.requests import Request

from config.secrets import resolve_secret

GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
AZURE_EXCHANGE_AUDIENCE = "api://AzureADTokenExchange"
IMDS = "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource="


def azure_credential_source(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Where google-auth fetches the Azure token to exchange. App Service and Functions inject
    IDENTITY_ENDPOINT + IDENTITY_HEADER; VMs and Container Apps expose IMDS."""
    env = env if env is not None else os.environ
    fmt = {"type": "json", "subject_token_field_name": "access_token"}
    if env.get("IDENTITY_ENDPOINT") and env.get("IDENTITY_HEADER"):
        sep = "&" if "?" in env["IDENTITY_ENDPOINT"] else "?"
        return {"url": f"{env['IDENTITY_ENDPOINT']}{sep}api-version=2019-08-01&resource={AZURE_EXCHANGE_AUDIENCE}",
                "headers": {"X-IDENTITY-HEADER": env["IDENTITY_HEADER"]}, "format": fmt}
    return {"url": IMDS + AZURE_EXCHANGE_AUDIENCE, "headers": {"Metadata": "True"}, "format": fmt}


def credentials_info(raw: str) -> dict[str, Any]:
    info = json.loads(raw)
    kind = info.get("type")
    if kind == "service_account":
        return info
    if kind == "external_account":
        if not info.get("service_account_impersonation_url"):
            raise ValueError("external_account config must impersonate a service account (service_account_impersonation_url)")
        return {**info, "credential_source": azure_credential_source()}
    raise ValueError(f"unsupported Google credential type {kind!r}")


def access_token(credentials_ref: str | None, scope: str) -> str:
    """Mint a short-lived bearer token. Raises SecretUnavailable when the reference cannot be resolved."""
    info = credentials_info(resolve_secret(credentials_ref))
    creds, _ = google.auth.load_credentials_from_dict(info, scopes=[scope])
    creds.refresh(Request())
    return creds.token
