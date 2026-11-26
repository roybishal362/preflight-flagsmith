"""Bootstrap a fresh self-hosted Flagsmith for Experimentation, idempotently, through its API.

Steps (from Flagsmith's "run locally" guide, fetched 2026-11-26):
  wait for /health/liveness/ -> GET /version/ -> create the first admin (POST /api/v1/auth/users/) or log in
  -> organisation -> project -> Development + Production environments
  -> warehouse "Flagsmith database" per environment -> organisation API key
Writes the resulting keys to selfhost/.env.local (git-ignored) and never prints secrets.
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

import requests


class Bootstrap:
    def __init__(self, base: str = "http://localhost:8000", log=print):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.log = log

    # -- helpers ----------------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def _call(self, method: str, path: str, **kw):
        r = self.s.request(method, self._url(path), timeout=30, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    @staticmethod
    def _items(payload):
        return payload.get("results", []) if isinstance(payload, dict) else (payload or [])

    # -- steps ------------------------------------------------------------------
    def wait_healthy(self, timeout: int = 300) -> None:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if requests.get(self._url("/health/liveness/"), timeout=5).status_code == 200:
                    self.log("✓ Flagsmith API is healthy")
                    return
            except requests.RequestException:
                pass
            time.sleep(3)
        raise RuntimeError(f"Flagsmith did not become healthy at {self.base} within {timeout}s. "
                           "Check `docker compose ps -a` (migrate-db must exit with code 0).")

    def version(self) -> dict:
        return self._call("GET", "/version/")

    def authenticate(self, email: str, password: str) -> None:
        v = self.version()
        exp = v.get("experimentation_enabled")
        if exp is False:
            raise RuntimeError("This Flagsmith has experimentation disabled. Set EXPERIMENTATION_ENABLED=true.")
        has_users = (v.get("self_hosted_data") or {}).get("has_users")
        if not has_users:
            res = self._call("POST", "/api/v1/auth/users/", json={
                "email": email, "password": password, "re_password": password,
                "first_name": "Preflight", "last_name": "Admin", "superuser": True})
            self.log(f"✓ Created first admin user {email}")
        else:
            res = self._call("POST", "/api/v1/auth/login/", json={"email": email, "password": password})
            self.log(f"✓ Logged in as {email}")
        self.s.headers["Authorization"] = f"Token {res['key']}"

    def ensure(self, list_path: str, create_path: str, match: dict, body: dict, what: str) -> dict:
        for item in self._items(self._call("GET", list_path)):
            if all(item.get(k) == v for k, v in match.items()):
                self.log(f"• {what} exists")
                return item
        item = self._call("POST", create_path, json=body)
        self.log(f"✓ Created {what}")
        return item

    def run(self, email: str, password: str, org_name: str = "Preflight Demo",
            project_name: str = "Preflight Demo", out: Path = Path("selfhost/.env.local")) -> dict:
        self.wait_healthy()
        self.authenticate(email, password)
        org = self.ensure("/api/v1/organisations/", "/api/v1/organisations/", {"name": org_name},
                          {"name": org_name}, f"organisation '{org_name}'")
        project = self.ensure(f"/api/v1/projects/?organisation={org['id']}", "/api/v1/projects/",
                              {"name": project_name}, {"name": project_name, "organisation": org["id"]},
                              f"project '{project_name}'")
        envs = {}
        for name in ("Development", "Production"):
            envs[name] = self.ensure(f"/api/v1/environments/?project={project['id']}", "/api/v1/environments/",
                                     {"name": name}, {"name": name, "project": project["id"]},
                                     f"environment '{name}'")
        for name, env in envs.items():
            key = env["api_key"]
            conns = self._items(self._call("GET", f"/api/v1/environments/{key}/warehouse-connections/"))
            if conns:
                self.log(f"• {name} warehouse exists (status {conns[0].get('status')})")
            else:
                c = self._call("POST", f"/api/v1/environments/{key}/warehouse-connections/",
                               json={"warehouse_type": "postgres", "config": {"source": "flagsmith_database"}})
                self.log(f"✓ Connected {name} to the Flagsmith-database warehouse (status {c.get('status')})")

        existing = {}
        if out.exists():
            for line in out.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    existing[k] = v
        api_key = existing.get("FLAGSMITH_API_KEY")
        if not api_key:
            res = self._call("POST", f"/api/v1/organisations/{org['id']}/master-api-keys/",
                             json={"name": "preflight", "is_admin": True})
            api_key = res["key"]
            self.log("✓ Created organisation API key 'preflight' (saved, not printed)")
        else:
            self.log("• API key already saved")

        values = {
            "FLAGSMITH_API_URL": f"{self.base}/api/v1/",
            "FLAGSMITH_SDK_API_URL": f"{self.base}/api/v1/",
            "FLAGSMITH_SDK_EVENTS_URL": f"{self.base}/api/",  # SDK appends v1/events; default would be Cloud
            "FLAGSMITH_API_KEY": api_key,
            "FLAGSMITH_ENVIRONMENT_KEY": envs["Development"]["api_key"],
            "FLAGSMITH_SMOKE_ENVIRONMENT_KEY": envs["Production"]["api_key"],
            "FLAGSMITH_PROJECT_ID": str(project["id"]),
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# Generated by `preflight selfhost bootstrap`. Git-ignored. Contains a secret.\n"
                       + "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        self.log(f"✓ Wrote {out} (Development = experiment env, Production = smoke-test env)")
        return {"organisation": org["id"], "project": project["id"],
                "environments": {k: v["api_key"] for k, v in envs.items()}}


def make_env_example(path: Path) -> None:
    """Write selfhost/.env with fresh random secrets if it doesn't exist."""
    if path.exists():
        return
    path.write_text(
        "# Secrets for the self-hosted Flagsmith in this folder. Git-ignored.\n"
        f"POSTGRES_PASSWORD={secrets.token_urlsafe(18)}\n"
        f"DJANGO_SECRET_KEY={secrets.token_urlsafe(40)}\n"
        f"WAREHOUSE_CREDENTIALS_SECRET={secrets.token_urlsafe(32)}\n"
        f"ADMIN_EMAIL=admin@preflight.local\n"
        f"ADMIN_PASSWORD={secrets.token_urlsafe(14)}\n", encoding="utf-8")


def bootstrap_from_files(folder: Path = Path("selfhost")) -> dict:
    env_file = folder / ".env"
    make_env_example(env_file)
    cfg = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    base = os.environ.get("FLAGSMITH_SELFHOST_URL", "http://localhost:8000")
    return Bootstrap(base).run(cfg["ADMIN_EMAIL"], cfg["ADMIN_PASSWORD"], out=folder / ".env.local")
