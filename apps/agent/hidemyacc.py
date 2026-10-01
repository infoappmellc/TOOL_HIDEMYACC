from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from playwright.async_api import Page, async_playwright

from .config import Config


class HideMyAccScanner:
    def __init__(self, config: Config) -> None:
        self.config = config

    def list_folder_profiles(self, folder_name: str) -> list[Dict[str, str]]:
        try:
            response = httpx.get(
                f"{self.config.hidemyacc_api}/profiles",
                params={"page": 1, "limit": 1000},
                timeout=60,
            )
            if response.status_code == 402:
                raise RuntimeError(
                    "Hidemyacc đang chạy và đã đăng nhập, nhưng Local API trả 402: "
                    "gói hiện tại chưa có quyền API hoặc quyền gói chưa được đồng bộ. "
                    "Quét folder cần gói Team/Business có API Access."
                )
            response.raise_for_status()
            payload = response.json()
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Không đọc được danh sách profile Hidemyacc: {exc}") from exc

        found: Dict[str, Dict[str, str]] = {}

        def walk(value: Any, inherited_folder: str = "") -> None:
            if isinstance(value, list):
                for item in value:
                    walk(item, inherited_folder)
                return
            if not isinstance(value, dict):
                return
            raw_folder = value.get("folderName") or value.get("groupName") or value.get("folder")
            current_folder = inherited_folder
            if isinstance(raw_folder, str):
                current_folder = raw_folder
            elif isinstance(raw_folder, dict):
                current_folder = str(raw_folder.get("name") or inherited_folder)
            profile_id = (
                value.get("id")
                or value.get("_id")
                or value.get("profileId")
                or value.get("profileID")
            )
            name = value.get("name") or value.get("profileName")
            if (
                profile_id
                and name
                and current_folder.casefold() == folder_name.casefold()
            ):
                found[str(profile_id)] = {
                    "hidemyacc_id": str(profile_id),
                    "name": str(name),
                }
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child, current_folder)

        walk(payload)
        return list(found.values())


class HideMyAccPublisher:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.active_api = config.hidemyacc_api

    async def publish(self, job: Dict[str, Any], video_path: Path) -> Optional[Path]:
        ws_url = self._start_profile(job["hidemyacc_id"])
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(ws_url)
            try:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()
                screenshot = await self._compose(page, job, video_path)
                return screenshot
            finally:
                # Do not call browser.close(): with CDP that would close the user's profile.
                if self.config.stop_profile_after:
                    self._stop_profile(job["hidemyacc_id"])

    def _start_profile(self, profile_id: str) -> str:
        candidates = [self.config.hidemyacc_api]
        if self.config.hidemyacc_api == "http://127.0.0.1:2268":
            candidates.append("http://127.0.0.1:12368")
        errors = []
        for api_base in candidates:
            try:
                response = httpx.post(f"{api_base}/profiles/start/{profile_id}", timeout=60)
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") != 1 or not payload.get("data", {}).get("wsUrl"):
                    errors.append(f"{api_base}: {payload.get('message', payload)}")
                    continue
                self.active_api = api_base
                return payload["data"]["wsUrl"]
            except Exception as exc:
                errors.append(f"{api_base}: {exc}")
        raise RuntimeError(
            "Không mở được Hidemyacc profile. Kiểm tra app đang chạy, đã đăng nhập, gói có API và port 2268/12368. "
            + " | ".join(errors)
        )

    def _stop_profile(self, profile_id: str) -> None:
        try:
            httpx.post(f"{self.active_api}/profiles/stop/{profile_id}", timeout=20)
        except Exception:
            pass

    async def _compose(self, page: Page, job: Dict[str, Any], video_path: Path) -> Optional[Path]:
        await page.goto(job["target_url"], wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(3500)

        if "login" in page.url.lower():
            raise RuntimeError("Facebook profile chưa đăng nhập hoặc phiên đã hết hạn")

        composer_pattern = re.compile(
            r"(Create (a )?post|Tạo bài viết|Bạn đang nghĩ gì|What's on your mind)", re.IGNORECASE
        )
        candidates = [
            page.get_by_role("button", name=composer_pattern),
            page.get_by_text(composer_pattern),
            page.locator('[contenteditable="true"]'),
        ]
        clicked = False
        for candidate in candidates:
            try:
                target = candidate.first
                await target.wait_for(state="visible", timeout=5000)
                await target.click()
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            raise RuntimeError("Không tìm thấy nút tạo bài viết. Facebook có thể đã đổi giao diện hoặc URL đích sai.")

        dialog = page.get_by_role("dialog").last
        await dialog.wait_for(state="visible", timeout=15000)
        file_inputs = dialog.locator('input[type="file"]')
        if await file_inputs.count() == 0:
            add_media = dialog.get_by_text(re.compile(r"(Photo/video|Ảnh/video)", re.IGNORECASE))
            if await add_media.count():
                await add_media.first.click()
                await page.wait_for_timeout(800)
        file_inputs = dialog.locator('input[type="file"]')
        if await file_inputs.count() == 0:
            raise RuntimeError("Không tìm thấy ô upload video trong composer")
        await file_inputs.first.set_input_files(str(video_path))

        caption = (job.get("caption") or "").strip()
        if caption:
            editors = dialog.locator('[contenteditable="true"]')
            if await editors.count() == 0:
                raise RuntimeError("Không tìm thấy ô nhập caption")
            await editors.first.fill(caption)

        await page.wait_for_timeout(5000)
        screenshot = video_path.parent / "composer-preview.png"
        await page.screenshot(path=str(screenshot), full_page=False)
        if self.config.dry_run:
            return screenshot

        post_button = dialog.get_by_role(
            "button", name=re.compile(r"^(Post|Đăng|Publish|Đăng bài)$", re.IGNORECASE)
        )
        await post_button.wait_for(state="visible", timeout=120000)
        for _ in range(60):
            if await post_button.is_enabled():
                break
            await page.wait_for_timeout(2000)
        if not await post_button.is_enabled():
            raise RuntimeError("Video chưa upload xong sau 2 phút")
        await post_button.click()
        await page.wait_for_timeout(5000)
        return screenshot
