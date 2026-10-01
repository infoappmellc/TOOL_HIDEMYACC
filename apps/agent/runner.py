from __future__ import annotations

import asyncio
import logging
import signal
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

import httpx

from .api import DashboardClient
from .config import Config
from .hidemyacc import HideMyAccPublisher, HideMyAccScanner
from .renderer import VideoRenderer


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("hma-agent")


class AgentRunner:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.api = DashboardClient(config)
        self.renderer = VideoRenderer(config)
        self.publisher = HideMyAccPublisher(config)
        self.scanner = HideMyAccScanner(config)
        self.running = True

    def stop(self, *_: object) -> None:
        logger.info("Đang dừng agent…")
        self.running = False

    def run(self) -> None:
        capabilities = {
            "ffmpeg": self.renderer.available(),
            "playwright": True,
            "dry_run": self.config.dry_run,
        }
        while self.running:
            try:
                agent_id = self.api.register(capabilities)
                logger.info("Đã kết nối dashboard với agent id %s", agent_id[:8])
                self._poll_loop()
            except (httpx.HTTPError, OSError) as exc:
                logger.error("Mất kết nối dashboard: %s; thử lại sau 10 giây", exc)
                self._sleep(10)
            except KeyboardInterrupt:
                self.stop()
        self.api.close()

    def _poll_loop(self) -> None:
        while self.running:
            try:
                sync_request = self.api.claim_sync()
                if sync_request:
                    self._process_sync(sync_request)
                    continue
                job = self.api.claim()
                if not job:
                    self._sleep(self.config.poll_seconds)
                    continue
                self._process(job)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {401, 404}:
                    raise
                logger.warning("API tạm lỗi: %s", exc)
                self._sleep(self.config.poll_seconds)
            except httpx.HTTPError:
                raise

    def _process_sync(self, request: dict) -> None:
        request_id = request["id"]
        folder_name = request["folder_name"]
        logger.info("Đang quét folder Hidemyacc %s", folder_name)
        try:
            profiles = self.scanner.list_folder_profiles(folder_name)
            self.api.finish_sync(request_id, "completed", profiles=profiles)
            logger.info("Đã đồng bộ %s profile từ folder %s", len(profiles), folder_name)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            logger.error("Đồng bộ Hidemyacc thất bại: %s", message)
            try:
                self.api.finish_sync(request_id, "failed", error=message)
            except Exception:
                logger.exception("Không thể báo trạng thái đồng bộ về dashboard")

    def _process(self, job: dict) -> None:
        job_id = job["id"]
        logger.info("Nhận job %s cho profile %s", job_id[:8], job["profile_name"])
        artifact: Optional[str] = None
        try:
            with self._heartbeat(job_id):
                self._event(job_id, "Bắt đầu dựng video")
                video_path = self.renderer.render(job)
                artifact = str(video_path)
                self._event(job_id, f"Đã tạo video: {video_path.name}")
                if job["kind"] == "create_only":
                    self._event(job_id, "Job chỉ tạo video — bỏ qua bước đăng Facebook")
                else:
                    mode = "DRY RUN" if self.config.dry_run else "LIVE"
                    self._event(job_id, f"Mở Hidemyacc và chuẩn bị composer ({mode})")
                    preview = asyncio.run(self.publisher.publish(job, video_path))
                    if self.config.dry_run:
                        self._event(job_id, f"Dry-run hoàn tất; chưa bấm Đăng. Preview: {preview}", "warning")
                    else:
                        self._event(job_id, "Đã gửi thao tác đăng bài tới Facebook")
            self.api.finish(job_id, "completed", artifact_path=artifact)
            logger.info("Job %s hoàn tất", job_id[:8])
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            logger.exception("Job %s thất bại", job_id[:8])
            self._event(job_id, message, "error")
            try:
                self.api.finish(job_id, "failed", artifact_path=artifact, error=message)
            except Exception:
                logger.exception("Không thể báo lỗi job về dashboard")

    def _event(self, job_id: str, message: str, level: str = "info") -> None:
        logger.info("%s | %s", job_id[:8], message)
        try:
            self.api.event(job_id, message, level)
        except httpx.HTTPError as exc:
            logger.warning("Không gửi được log lên dashboard: %s", exc)

    @contextmanager
    def _heartbeat(self, job_id: str) -> Iterator[None]:
        stopped = threading.Event()

        def send() -> None:
            while not stopped.wait(20):
                try:
                    self.api.heartbeat(job_id)
                except Exception as exc:
                    logger.warning("Heartbeat thất bại: %s", exc)

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=2)

    def _sleep(self, seconds: int) -> None:
        for _ in range(seconds):
            if not self.running:
                break
            time.sleep(1)


def main() -> None:
    config = Config()
    runner = AgentRunner(config)
    signal.signal(signal.SIGINT, runner.stop)
    signal.signal(signal.SIGTERM, runner.stop)
    runner.run()
