from __future__ import annotations

import socket
from typing import Any, Dict, Optional

import httpx

from .config import Config


class DashboardClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.agent_id: Optional[str] = None
        self.client = httpx.Client(
            base_url=config.server_url,
            headers={"Authorization": f"Bearer {config.worker_token}"},
            timeout=config.request_timeout,
        )

    def close(self) -> None:
        self.client.close()

    def register(self, capabilities: Dict[str, Any]) -> str:
        response = self.client.post(
            "/api/agent/register",
            json={
                "name": self.config.agent_name,
                "version": "0.1.0",
                "hostname": socket.gethostname(),
                "capabilities": capabilities,
            },
        )
        response.raise_for_status()
        self.agent_id = response.json()["id"]
        return self.agent_id

    def heartbeat(self, current_job_id: Optional[str] = None) -> None:
        self._require_agent()
        response = self.client.post(
            f"/api/agent/{self.agent_id}/heartbeat", json={"current_job_id": current_job_id}
        )
        response.raise_for_status()

    def claim(self) -> Optional[Dict[str, Any]]:
        self._require_agent()
        response = self.client.post(f"/api/agent/{self.agent_id}/claim")
        response.raise_for_status()
        return response.json().get("job")

    def claim_sync(self) -> Optional[Dict[str, Any]]:
        self._require_agent()
        response = self.client.post(f"/api/agent/{self.agent_id}/sync/claim")
        response.raise_for_status()
        return response.json().get("request")

    def finish_sync(
        self,
        request_id: str,
        status: str,
        profiles: Optional[list] = None,
        error: Optional[str] = None,
    ) -> None:
        self._require_agent()
        response = self.client.post(
            f"/api/agent/{self.agent_id}/sync/{request_id}/finish",
            json={"status": status, "profiles": profiles or [], "error": error},
        )
        response.raise_for_status()

    def event(self, job_id: str, message: str, level: str = "info") -> None:
        self._require_agent()
        response = self.client.post(
            f"/api/agent/{self.agent_id}/jobs/{job_id}/events",
            json={"level": level, "message": message[:2000]},
        )
        response.raise_for_status()

    def finish(
        self,
        job_id: str,
        status: str,
        artifact_path: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        self._require_agent()
        response = self.client.post(
            f"/api/agent/{self.agent_id}/jobs/{job_id}/finish",
            json={"status": status, "artifact_path": artifact_path, "error": error},
        )
        response.raise_for_status()

    def _require_agent(self) -> None:
        if not self.agent_id:
            raise RuntimeError("Agent chưa đăng ký với dashboard")
