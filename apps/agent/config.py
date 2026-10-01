from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    server_url: str = os.getenv("SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
    worker_token: str = os.getenv("WORKER_TOKEN", "dev-worker-token")
    agent_name: str = os.getenv("AGENT_NAME", "local-agent")
    poll_seconds: int = int(os.getenv("POLL_SECONDS", "5"))
    hidemyacc_api: str = os.getenv("HIDEMYACC_API", "http://127.0.0.1:2268").rstrip("/")
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", "./output")).expanduser().resolve()
    ffmpeg_bin: str = os.getenv("FFMPEG_BIN", "ffmpeg")
    dry_run: bool = env_bool("DRY_RUN", True)
    stop_profile_after: bool = env_bool("STOP_PROFILE_AFTER", False)
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "30"))
