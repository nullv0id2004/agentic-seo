import json

import pytest

from collectors.google_auth import azure_credential_source, credentials_info


def test_app_service_identity_endpoint_is_used():
    src = azure_credential_source({"IDENTITY_ENDPOINT": "http://127.0.0.1:41234/msi/token", "IDENTITY_HEADER": "abc"})
    assert src["url"] == "http://127.0.0.1:41234/msi/token?api-version=2019-08-01&resource=api://AzureADTokenExchange"
    assert src["headers"] == {"X-IDENTITY-HEADER": "abc"}
    assert src["format"]["subject_token_field_name"] == "access_token"


def test_vm_falls_back_to_imds():
    src = azure_credential_source({})
    assert src["url"].startswith("http://169.254.169.254/") and src["headers"] == {"Metadata": "True"}


def test_external_account_gets_runtime_credential_source(monkeypatch):
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://127.0.0.1:1/msi/token")
    monkeypatch.setenv("IDENTITY_HEADER", "h")
    cfg = {"type": "external_account", "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/p/providers/azure",
           "subject_token_type": "urn:ietf:params:oauth:token-type:jwt", "token_url": "https://sts.googleapis.com/v1/token",
           "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/seo-korum@x.iam.gserviceaccount.com:generateAccessToken",
           "credential_source": {"url": "placeholder"}}
    info = credentials_info(json.dumps(cfg))
    assert info["credential_source"]["url"].startswith("http://127.0.0.1:1/msi/token?")
    with pytest.raises(ValueError):
        credentials_info(json.dumps({**cfg, "service_account_impersonation_url": None}))
    with pytest.raises(ValueError):
        credentials_info(json.dumps({"type": "authorized_user"}))


def test_service_account_passes_through():
    info = credentials_info(json.dumps({"type": "service_account", "client_email": "a@b"}))
    assert info["client_email"] == "a@b"
