"""Thin client for the Flagsmith Admin API (experiments, metrics, warehouse, flags).

Endpoints are the ones documented in Flagsmith's "set up with a coding agent" guide.
Auth: an Organisation API key, sent as `Authorization: Api-Key <key>`.
Every call that changes something is recorded in the audit log by the caller.
"""
from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_API = "https://api.flagsmith.com/api/v1/"


class FlagsmithError(RuntimeError):
    pass


class FlagsmithAdmin:
    def __init__(self, api_key: str | None = None, api_url: str | None = None, timeout: int = 20):
        self.api_key = api_key or os.environ.get("FLAGSMITH_API_KEY", "")
        if not self.api_key:
            raise FlagsmithError("FLAGSMITH_API_KEY is not set. Add it to .env (an Organisation API key).")
        self.base = (api_url or os.environ.get("FLAGSMITH_API_URL") or DEFAULT_API).rstrip("/") + "/"
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Api-Key {self.api_key}", "Accept": "application/json"})

    # -- low level -----------------------------------------------------------
    def _req(self, method: str, path: str, **kw: Any) -> Any:
        url = self.base + path.lstrip("/")
        r = self.s.request(method, url, timeout=self.timeout, **kw)
        if r.status_code >= 400:
            raise FlagsmithError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def get(self, path: str, **params: Any) -> Any:
        return self._req("GET", path, params=params or None)

    def post(self, path: str, body: dict | None = None) -> Any:
        return self._req("POST", path, json=body or {})

    @staticmethod
    def items(payload: Any) -> list:
        """Flagsmith list endpoints are sometimes paginated ({results: [...]}) and sometimes bare lists."""
        if isinstance(payload, dict) and "results" in payload:
            return payload["results"]
        return payload if isinstance(payload, list) else []

    # -- experimentation -----------------------------------------------------
    def warehouse_connections(self, env_key: str) -> list:
        return self.items(self.get(f"environments/{env_key}/warehouse-connections/"))

    def experiments(self, env_key: str) -> list:
        return self.items(self.get(f"environments/{env_key}/experiments/"))

    def experiment(self, env_key: str, exp_id: int) -> dict:
        return self.get(f"environments/{env_key}/experiments/{exp_id}/")

    def metrics(self, env_key: str) -> list:
        return self.items(self.get(f"environments/{env_key}/experiment-metrics/"))

    def results(self, env_key: str, exp_id: int) -> Any:
        return self.get(f"environments/{env_key}/experiments/{exp_id}/results/")

    def refresh_results(self, env_key: str, exp_id: int) -> Any:
        """Allowed once every 5 minutes per experiment (docs)."""
        return self.post(f"environments/{env_key}/experiments/{exp_id}/results/refresh/")

    # -- flags ----------------------------------------------------------------
    def features(self, project_id: int, search: str | None = None) -> list:
        params = {"search": search} if search else {}
        return self.items(self.get(f"projects/{project_id}/features/", **params))

    def multivariate_options(self, project_id: int, feature_id: int) -> list:
        return self.items(self.get(f"projects/{project_id}/features/{feature_id}/mv-options/"))
