"""Credential resolution. projects.credentials_ref names a Key Vault secret; nothing stores a raw secret.

Backends:
  keyvault  Azure Key Vault via DefaultAzureCredential (managed identity on App Service).
  env       For local development and tests only: SEO_SECRET_<REF> environment variables.

A missing secret raises. Nothing here returns a default. (P3: fail closed.)
"""
from __future__ import annotations

import os
from functools import lru_cache

from config.settings import get_settings


class SecretUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _keyvault_client():
    try:
        from azure.identity import DefaultAzureCredential  # type: ignore
        from azure.keyvault.secrets import SecretClient  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise SecretUnavailable("azure-identity and azure-keyvault-secrets are not installed") from e
    url = get_settings().key_vault_url
    if not url:
        raise SecretUnavailable("AZURE_KEY_VAULT_URL is not set")
    return SecretClient(vault_url=url, credential=DefaultAzureCredential())


def resolve_secret(ref: str | None) -> str:
    """Resolve a credential reference to its value. Raises SecretUnavailable rather than guessing."""
    if not ref:
        raise SecretUnavailable("empty credentials_ref")
    backend = get_settings().secret_backend
    if backend == "env":
        key = "SEO_SECRET_" + ref.upper().replace("-", "_")
        val = os.environ.get(key)
        if not val:
            raise SecretUnavailable(f"{key} is not set")
        return val
    if backend == "keyvault":
        try:
            return _keyvault_client().get_secret(ref).value
        except SecretUnavailable:
            raise
        except Exception as e:
            raise SecretUnavailable(f"key vault lookup for {ref!r} failed: {type(e).__name__}") from e
    raise SecretUnavailable(f"unknown secret backend {backend!r}")
