from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import Locator, Page, async_playwright
from websockets.sync.client import connect as websocket_connect


class BrowserBridge:
    """Connect to a Hidemyacc Chromium profile using the same CDP pattern as GPM."""

    hidemyacc_cdp = "http://127.0.0.1:9333"

    def __init__(self) -> None:
        self.state_file = Path(__file__).resolve().parents[2] / "data" / "browser-bridge.json"

    def status(self) -> dict[str, Any]:
        saved = self._saved_state()
        endpoint = str(saved.get("cdp_url", ""))
        if endpoint and self._is_cdp(endpoint):
            return {"connected": True, "cdp_url": endpoint, "profile_name": saved.get("profile_name", "")}
        managed = self._managed_connection(str(saved.get("profile_name", "")))
        if managed and self._is_cdp(managed["cdp_url"]):
            self._save(managed["cdp_url"], managed["profile_name"])
            return {"connected": True, **managed}
        discovered = self._discover_cdp()
        if discovered:
            self._save(discovered)
            return {"connected": True, "cdp_url": discovered}
        return {"connected": False, "cdp_url": "", "browser_running": self._marco_process() is not None}

    def connect_profile(self, profile_name: str) -> dict[str, Any]:
        current = self.status()
        if current["connected"] and current.get("profile_name") == profile_name:
            return {**current, "profile_name": profile_name}
        self._ensure_hidemyacc_control()
        process = self._marco_process()
        if process:
            running_name = str(current.get("profile_name", "")) or self._running_hidemyacc_profile() or profile_name
            self._close_hidemyacc_profile(running_name, process[0])
        result = self._start_hidemyacc_profile(profile_name)
        port = int(result.get("port", 0))
        if not result.get("success") or not port:
            raise RuntimeError(f"Hidemyacc không mở được profile {profile_name} ở chế độ điều khiển")
        endpoint = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20
        while time.time() < deadline:
            if self._is_cdp(endpoint):
                self._save(endpoint, profile_name)
                return {"connected": True, "cdp_url": endpoint, "profile_name": profile_name}
            time.sleep(.5)
        raise RuntimeError("Browser đã mở lại nhưng chưa bật được cổng điều khiển CDP")

    def endpoint(self) -> str:
        status = self.status()
        if not status["connected"]:
            raise RuntimeError("Browser chưa kết nối. Mở profile Hidemyacc rồi bấm Kết nối browser")
        return str(status["cdp_url"])

    def _saved_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_file.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self, endpoint: str, profile_name: str = "") -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps({"cdp_url": endpoint, "profile_name": profile_name}), encoding="utf-8"
        )

    @staticmethod
    def _is_cdp(endpoint: str) -> bool:
        try:
            response = httpx.get(f"{endpoint}/json/version", timeout=1)
            return response.status_code == 200 and bool(response.json().get("webSocketDebuggerUrl"))
        except Exception:
            return False

    def _discover_cdp(self) -> str:
        for port in range(9222, 9251):
            endpoint = f"http://127.0.0.1:{port}"
            if self._is_cdp(endpoint):
                return endpoint
        return ""

    @staticmethod
    def _stop_marco(pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.time() + 12
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.25)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.1)

    @staticmethod
    def _hidemyacc_running() -> bool:
        return subprocess.run(["pgrep", "-x", "HideMyAcc-3"], capture_output=True).returncode == 0

    def _ensure_hidemyacc_control(self) -> None:
        # Hidemyacc's internal Run function can be called only when its local Chromium
        # inspector is enabled. This is UI automation and doesn't use the paid API.
        if self._hidemyacc_running() and not self._hidemyacc_page_target():
            subprocess.run(
                ["osascript", "-e", 'tell application "HideMyAcc-3" to quit'],
                capture_output=True,
            )
            deadline = time.time() + 15
            while time.time() < deadline and self._hidemyacc_running():
                time.sleep(.25)
            process = self._marco_process()
            if process:
                self._stop_marco(process[0])

        if not self._hidemyacc_running():
            env = os.environ.copy()
            env.pop("ELECTRON_RUN_AS_NODE", None)
            subprocess.Popen(
                [
                    "/Applications/HideMyAcc-3.app/Contents/MacOS/HideMyAcc-3",
                    "--remote-debugging-port=9333",
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        deadline = time.time() + 25
        ready = False
        while time.time() < deadline:
            probe = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to tell process "HideMyAcc-3" to count windows',
                ],
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0 and probe.stdout.strip() not in {"", "0"}:
                ready = True
                break
            time.sleep(.5)
        if not ready:
            raise RuntimeError("Hidemyacc không khởi động được hoặc chưa hiện cửa sổ Profiles")
        if not self._hidemyacc_page_target():
            raise RuntimeError("Không bật được chế độ điều khiển giao diện local của Hidemyacc")

    def _running_hidemyacc_profile(self) -> str:
        value = self._evaluate_hidemyacc(
            """(() => {
  const stop = document.querySelector('tr button.btn-stop');
  return stop?.closest('tr')?.querySelector('.profile-name')?.textContent.trim() || '';
})()"""
        )
        return str(value or "")

    def _managed_connection(self, profile_name: str = "") -> dict[str, str] | None:
        if not self._hidemyacc_page_target():
            return None
        encoded_name = json.dumps(profile_name)
        expression = f'''(async () => {{
  const runners = await window.hidemyacc.app.exec({{sourceCode: `
    return Object.entries(global.browsers || {{}}).map(([id, item]) => ({{
      id, action: item.action, wsData: item.wsData || null
    }}));
  `}});
  const profiles = window.profiles || [];
  const preferred = {encoded_name};
  const candidates = runners
    .map(item => ({{...item, name: profiles.find(p => p.id === item.id)?.name || ''}}))
    .filter(item => item.wsData?.port);
  return candidates.find(item => item.name === preferred) || candidates[0] || null;
}})()'''
        try:
            result = self._evaluate_hidemyacc(expression)
            if not isinstance(result, dict):
                return None
            port = int(result.get("wsData", {}).get("port", 0))
            name = str(result.get("name", ""))
            if not port or not name:
                return None
            return {"cdp_url": f"http://127.0.0.1:{port}", "profile_name": name}
        except Exception:
            return None

    def _start_hidemyacc_profile(self, profile_name: str) -> dict[str, Any]:
        encoded_name = json.dumps(profile_name)
        profile_id = self._evaluate_hidemyacc(
            f"window.profiles.find(item => item.name === {encoded_name})?.id || ''"
        )
        if not profile_id:
            raise RuntimeError(f"Không tìm thấy profile {profile_name} trong Hidemyacc")
        source = (
            f"return await global.startBrowserProfile({json.dumps(profile_id)}, "
            "undefined, '', undefined);"
        )
        expression = f"window.hidemyacc.app.exec({{sourceCode:{json.dumps(source)}}})"
        result = self._evaluate_hidemyacc(expression, timeout=180)
        return result if isinstance(result, dict) else {"success": False}

    def _hidemyacc_page_target(self) -> str:
        try:
            response = httpx.get(f"{self.hidemyacc_cdp}/json/list", timeout=1)
            response.raise_for_status()
            targets = response.json()
            for target in targets:
                if target.get("type") == "page" and "/profiles" in str(target.get("url", "")):
                    return str(target.get("webSocketDebuggerUrl", ""))
        except Exception:
            pass
        return ""

    def _evaluate_hidemyacc(self, expression: str, timeout: float = 5) -> Any:
        target = self._hidemyacc_page_target()
        if not target:
            raise RuntimeError("Không kết nối được giao diện local của Hidemyacc")
        with websocket_connect(target, open_timeout=3, close_timeout=1) as websocket:
            websocket.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": expression,
                            "returnByValue": True,
                            "awaitPromise": True,
                        },
                    }
                )
            )
            while True:
                message = json.loads(websocket.recv(timeout=timeout))
                if message.get("id") != 1:
                    continue
                result = message.get("result", {})
                if result.get("exceptionDetails"):
                    description = (
                        result.get("result", {}).get("description")
                        or result["exceptionDetails"].get("text")
                    )
                    raise RuntimeError(str(description))
                return result.get("result", {}).get("value")

    def _run_profile_from_dom(self, profile_name: str) -> None:
        encoded_name = json.dumps(profile_name)
        inspect = f'''(() => {{
  const name = {encoded_name};
  const profile = [...document.querySelectorAll('.profile-name')]
    .find(item => item.textContent.trim() === name);
  if (!profile) return 'missing';
  const row = profile.closest('tr');
  const run = row && row.querySelector('button.btn-run');
  if (run) {{ run.click(); return 'clicked'; }}
  return row && row.querySelector('button.btn-stop') ? 'running' : 'unavailable';
}})()'''
        force_requested = False
        deadline = time.time() + 120
        state = ""
        while time.time() < deadline:
            state = str(self._evaluate_hidemyacc(inspect))
            if state == "clicked":
                return
            if state == "missing":
                raise RuntimeError(f"Không tìm thấy profile {profile_name} trong Hidemyacc")
            # Recover a stale Running state only when there is no local Marco process.
            if state == "running" and self._marco_process() is None and not force_requested:
                force_requested = True
                force_close = f'''(() => {{
  const name = {encoded_name};
  const profile = [...document.querySelectorAll('.profile-name')]
    .find(item => item.textContent.trim() === name);
  const stop = profile && profile.closest('tr')?.querySelector('button.btn-stop');
  if (!stop) return false;
  stop.click();
  return true;
}})()'''
                if self._evaluate_hidemyacc(force_close):
                    confirm = '''(() => {
  const dialog = document.querySelector('[role="dialog"]');
  const button = dialog && [...dialog.querySelectorAll('button')]
    .find(item => item.textContent.trim() === 'Force Close');
  if (!button) return false;
  button.click();
  return true;
})()'''
                    for _ in range(8):
                        time.sleep(.5)
                        if self._evaluate_hidemyacc(confirm):
                            break
                        # The row may have moved straight to Syncing, with no dialog.
                        next_state = str(self._evaluate_hidemyacc(inspect))
                        if next_state == "clicked":
                            return
                        if next_state != "running":
                            break
            time.sleep(.5)
        raise RuntimeError(f"Profile {profile_name} chưa sẵn sàng để Run (trạng thái: {state})")

    def _close_hidemyacc_profile(self, profile_name: str, pid: int) -> None:
        if not self._hidemyacc_page_target():
            self._stop_marco(pid)
            return
        encoded_name = json.dumps(profile_name)
        profile_id = self._evaluate_hidemyacc(
            f"window.profiles.find(item => item.name === {encoded_name})?.id || ''"
        )
        if profile_id:
            source = f'''const id = {json.dumps(profile_id)};
if (global.browsers && global.browsers[id]) {{
  await global.stopBrowserProfile(id);
  return true;
}}
return false;'''
            expression = f"window.hidemyacc.app.exec({{sourceCode:{json.dumps(source)}}})"
            if self._evaluate_hidemyacc(expression, timeout=180):
                deadline = time.time() + 30
                while time.time() < deadline:
                    state = self._evaluate_hidemyacc(
                        f'''(() => {{
  const profile = [...document.querySelectorAll('.profile-name')]
    .find(item => item.textContent.trim() === {encoded_name});
  const row = profile && profile.closest('tr');
  return !!(row && row.querySelector('button.btn-run'));
}})()'''
                    )
                    if state:
                        return
                    time.sleep(.5)
                return
        open_dialog = f'''(() => {{
  const name = {encoded_name};
  const profile = [...document.querySelectorAll('.profile-name')]
    .find(item => item.textContent.trim() === name);
  const stop = profile && profile.closest('tr')?.querySelector('button.btn-stop');
  if (!stop) return false;
  stop.click();
  return true;
}})()'''
        if not self._evaluate_hidemyacc(open_dialog):
            self._stop_marco(pid)
            return
        time.sleep(.5)
        confirm = '''(() => {
  const dialog = document.querySelector('[role="dialog"]');
  const button = dialog && [...dialog.querySelectorAll('button')]
    .find(item => ['Stop', 'Force Close'].includes(item.textContent.trim()));
  if (!button) return false;
  button.click();
  return true;
})()'''
        # Normal local sessions stop immediately and show no dialog. Stale/remote
        # sessions show a confirmation dialog with Stop or Force Close.
        self._evaluate_hidemyacc(confirm)
        deadline = time.time() + 25
        closed = False
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                closed = True
                break
            time.sleep(.25)
        if not closed:
            self._stop_marco(pid)

        # Hidemyacc performs a backup/sync after closing. Starting Marco before the
        # row is Ready lets the manager replace it later and drops the CDP port.
        ready = f'''(() => {{
  const name = {encoded_name};
  const profile = [...document.querySelectorAll('.profile-name')]
    .find(item => item.textContent.trim() === name);
  const row = profile && profile.closest('tr');
  return !!(row && row.querySelector('button.btn-run') &&
    row.querySelector('.status')?.textContent.trim() === 'Ready');
}})()'''
        deadline = time.time() + 120
        while time.time() < deadline:
            if self._evaluate_hidemyacc(ready):
                return
            time.sleep(.5)
        raise RuntimeError(f"Hidemyacc chưa đồng bộ xong profile {profile_name}")

    @staticmethod
    def _marco_process() -> tuple[int, list[str]] | None:
        output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            pid_text, command = line.split(None, 1)
            if "/Marco.app/Contents/MacOS/Marco" not in command or "--user-data-dir=" not in command:
                continue
            return int(pid_text), shlex.split(command)
        return None


class FacebookPublisher:
    def __init__(self, bridge: BrowserBridge) -> None:
        self.bridge = bridge

    async def publish(
        self,
        page_id: str,
        page_name: str,
        video_path: Path,
        source_url: str,
        dry_run: bool = True,
    ) -> dict[str, str]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(self.bridge.endpoint())
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            await self._switch_page(page, page_id, page_name)
            previous_posts = {item["key"] for item in await self._feed_posts(page, page_id)} if not dry_run else set()
            screenshot, post_url = await self._upload_reel(page, page_id, page_name, video_path, dry_run)
            if dry_run:
                return {"preview": str(screenshot), "post_url": ""}
            await page.wait_for_timeout(20000)
            post_url = await self._comment_on_recent_post(page, page_id, source_url, previous_posts)
            return {"preview": str(screenshot), "post_url": post_url}

    async def _assert_page_identity(self, page: Page, page_id: str) -> None:
        cookies = await page.context.cookies("https://www.facebook.com/")
        actor_id = next((item["value"] for item in cookies if item["name"] == "i_user"), "")
        if actor_id != page_id:
            raise RuntimeError(f"Facebook chưa hoạt động dưới đúng Page ID {page_id}; đã dừng trước khi đăng")

    async def _switch_page(self, page: Page, page_id: str, page_name: str) -> None:
        # Follow TOOL-GPM: home -> account menu -> profile list -> exact Page row.
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)
        if "login" in page.url.lower():
            raise RuntimeError("Facebook chưa đăng nhập trong profile này")
        account_pattern = re.compile(
            r"^(Your profile|Account|Trang cá nhân của bạn|Tài khoản|Twoje konto)$", re.I
        )
        account = page.get_by_role("button", name=account_pattern)
        await account.first.click(timeout=10000)
        menu = page.locator('[role="dialog"]:visible, [role="menu"]:visible').last
        await menu.wait_for(state="visible", timeout=10000)
        all_profiles = menu.get_by_text(re.compile(
            r"^(See all profiles|See all Pages|Xem tất cả trang cá nhân|Zobacz wszystkie profile)$", re.I
        ))
        if await all_profiles.count():
            await all_profiles.first.click()
            await page.wait_for_timeout(800)

        # Stored Page titles can include a location suffix absent from the switcher.
        display_name = page_name.split(" | ", 1)[0].strip()
        row_pattern = re.compile(r"^" + re.escape(display_name) + r"(?:,|$)", re.I)
        menu = page.locator('[role="dialog"]:visible, [role="menu"]:visible').last
        rows = menu.get_by_role("button", name=row_pattern).or_(
            menu.get_by_role("link", name=row_pattern)
        ).and_(page.locator(":visible"))
        if not await rows.count():
            search = menu.get_by_role("textbox", name=re.compile(
                r"profiles? and pages?|trang cá nhân và trang|profile i strony", re.I
            )).or_(menu.get_by_placeholder(re.compile(
                r"profiles? and pages?|trang cá nhân và trang|profile i strony", re.I
            )))
            if await search.count():
                await search.first.fill(display_name)
                await rows.first.wait_for(state="visible", timeout=10000)
        if await rows.count() != 1:
            raise RuntimeError(f"Không tìm thấy duy nhất Page {display_name} trong danh sách profile Facebook")
        await rows.first.click()
        for _ in range(20):
            try:
                await self._assert_page_identity(page, page_id)
                break
            except RuntimeError:
                await page.wait_for_timeout(500)
        else:
            raise RuntimeError(f"Chuyển sang Page {display_name} không thành công; đã dừng trước khi tải video")
        await page.goto(self._page_url(page_id), wait_until="domcontentloaded", timeout=90000)
        await self._assert_page_identity(page, page_id)

    async def _upload_reel(
        self, page: Page, page_id: str, page_name: str, video_path: Path, dry_run: bool
    ) -> tuple[Path, str]:
        await page.goto(self._page_url(page_id), wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(3000)
        await self._assert_page_identity(page, page_id)
        # The Reels tab opens the feed, not the upload composer.
        create_pattern = re.compile(r"^(Create reel|Create a reel|Tạo thước phim|Thước phim|Utwórz rolkę|Rolka|Reel)$", re.I)
        create_links = re.compile(r"^(Create reel|Create a reel|Tạo thước phim|Utwórz rolkę)$", re.I)
        create = page.get_by_role("link", name=create_links).or_(page.get_by_role("button", name=create_pattern))
        create = create.and_(page.locator(":visible"))
        if not await create.count():
            raise RuntimeError("Không tìm thấy nút tạo Thước phim trên Page; kiểm tra quyền và danh tính Page")
        await create.first.click()
        await page.wait_for_timeout(2500)
        # Scope to the uploader so background and injected inputs cannot receive
        # the file while leaving the upload dialog open.
        file_input = page.locator('[role="dialog"] input[type="file"][accept^="video/"]')
        if not await file_input.count():
            file_input = page.locator('input[type="file"][accept^="video/"]')
        if await file_input.count() == 0:
            upload = page.get_by_text(re.compile(r"(Add video|Thêm video|Dodaj film|Upload)", re.I))
            if await upload.count():
                await upload.first.click()
                await page.wait_for_timeout(700)
        dialog_inputs = page.locator('[role="dialog"] input[type="file"][accept^="video/"]')
        if await dialog_inputs.count():
            file_input = dialog_inputs
        try:
            await file_input.last.wait_for(state="attached", timeout=15000)
        except Exception as exc:
            raise RuntimeError("Không tìm thấy ô tải video Reel") from exc
        await file_input.last.set_input_files(str(video_path))
        await page.wait_for_timeout(5000)

        publish_pattern = re.compile(r"^(Post|Share reel|Publish|Đăng|Opublikuj)$", re.I)
        next_pattern = re.compile(r"^(Next|Tiếp|Dalej)$", re.I)
        for _ in range(2):
            if await page.get_by_role("button", name=publish_pattern).and_(page.locator(":visible")).count():
                break
            next_button = page.get_by_role("button", name=next_pattern).and_(page.locator(":visible"))
            if not await next_button.count():
                break
            await next_button.last.click()
            await page.wait_for_timeout(2500)

        publish = page.get_by_role("button", name=publish_pattern).and_(page.locator(":visible"))
        if not await publish.count():
            share_pattern = re.compile(r"^(Share|Chia sẻ|Udostępnij)$", re.I)
            publish = page.get_by_role("button", name=share_pattern).filter(
                has_text=share_pattern
            ).and_(page.locator(":visible"))
        if await publish.count() == 0:
            raise RuntimeError("Không tìm thấy nút đăng Reel")
        button = publish.last
        for _ in range(60):
            if await button.is_enabled():
                break
            await page.wait_for_timeout(2000)
        if not await button.is_enabled():
            raise RuntimeError("Facebook chưa xử lý xong video sau 2 phút")

        # The description stays empty; the news URL belongs in the comment.
        description_pattern = re.compile(r"describe your reel|mô tả thước phim|opisz.*rolk", re.I)
        descriptions = page.get_by_role("textbox", name=description_pattern).or_(
            page.get_by_placeholder(description_pattern)
        ).and_(page.locator(":visible"))
        if await descriptions.count():
            await descriptions.first.fill("")
        await self._assert_page_identity(page, page_id)
        preview = video_path.parent / "facebook-preview.png"
        await page.screenshot(path=str(preview), full_page=False)
        if dry_run:
            return preview, ""
        await button.click()
        # Notification links can point to unrelated or old Reels.
        match = re.match(r"https://www\.facebook\.com/reel/(\d+)(?:[/?]|$)", page.url)
        return preview, f"https://www.facebook.com/reel/{match.group(1)}" if match else ""

    async def _feed_posts(self, page: Page, page_id: str) -> list[dict[str, Any]]:
        return await page.evaluate(r'''(pageId) => {
            const visible = el => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            };
            const minutes = text => {
                text = text.trim().toLocaleLowerCase();
                if (/^(vừa xong|vừa mới|just now|a moment ago|przed chwilą)$/.test(text)) return 0;
                if (/^(\d+\s*(giây( trước)?|seconds?( ago)?|s|sek\.?))$/.test(text)) return 0;
                const match = text.match(/^(\d+)\s*(phút( trước)?|minutes?( ago)?|mins?( ago)?|m|min\.?|minut[ay]? temu)$/);
                return match ? Number(match[1]) : null;
            };
            const posts = [], seen = new Set();
            const controls = [...document.querySelectorAll('button,[role="button"]')].filter(el =>
                visible(el) && /^(viết bình luận|bình luận|comment|leave a comment|write a comment|komentarz|skomentuj)$/i.test(
                    (el.getAttribute('aria-label') || el.innerText || '').trim()
                ));
            for (const control of controls) {
                let post = control.parentElement;
                for (let depth = 0; post && depth < 20; depth++, post = post.parentElement) {
                    if (post === document.body || post.getAttribute('role') === 'feed' || post.getAttribute('role') === 'main') break;
                    if (!post.querySelector('video')) continue;
                    const links = [...post.querySelectorAll('a[href]')];
                    const author = links.find(a => {
                        try { return new URL(a.href).searchParams.has('id'); } catch { return false; }
                    });
                    if (!author || new URL(author.href).searchParams.get('id') !== pageId) continue;
                    if (seen.has(post)) break;
                    seen.add(post);
                    const lines = post.innerText.split('\n').map(x => x.trim()).filter(Boolean);
                    const time = lines.slice(0, 60).find(x => minutes(x) !== null);
                    const permalink = links.find(a => /\/(reel|posts|videos)\/[^/?]+|story_fbid=/.test(a.href) && !/notif/i.test(a.href));
                    let url = '';
                    if (permalink) {
                        const parsed = new URL(permalink.href);
                        if (parsed.searchParams.has('story_fbid')) {
                            url = parsed.origin + '/permalink.php?story_fbid=' + parsed.searchParams.get('story_fbid') + '&id=' + pageId;
                        } else url = parsed.origin + parsed.pathname;
                    }
                    const poster = post.querySelector('video')?.poster || '';
                    const textKey = lines.filter(x => !/^Facebook$/.test(x) && !/^(\d+\s*(phút|giây|giờ|ngày|minutes?|hours?|days?|m|s)\b|vừa xong|just now|przed chwilą)/i.test(x)).join(' ').slice(0, 500);
                    const key = url || poster.split('?')[0] || textKey;
                    const index = posts.length;
                    post.setAttribute('data-hma-feed-post', String(index));
                    posts.push({key, url, minutes: time ? minutes(time) : null, index});
                    break;
                }
            }
            return posts;
        }''', page_id)

    @staticmethod
    def _select_recent_post(posts: list[dict[str, Any]], previous_posts: set[str]) -> dict[str, Any] | None:
        candidates = [item for item in posts if item["key"] not in previous_posts
                      and item["minutes"] is not None and 0 <= item["minutes"] <= 5]
        if not candidates:
            return None
        newest = min(item["minutes"] for item in candidates)
        matches = [item for item in candidates if item["minutes"] == newest]
        if len(matches) != 1:
            raise RuntimeError("Có nhiều bài mới cùng thời gian; chưa xác định được bài vừa đăng để bình luận")
        return matches[0]

    async def _comment_on_recent_post(
        self, page: Page, page_id: str, source_url: str, previous_posts: set[str]
    ) -> str:
        for attempt in range(4):
            await page.goto(self._page_url(page_id), wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(2500)
            await self._assert_page_identity(page, page_id)
            for _ in range(8):
                posts = await self._feed_posts(page, page_id)
                selected = self._select_recent_post(posts, previous_posts)
                if selected:
                    post = page.locator(f'[data-hma-feed-post="{selected["index"]}"]')
                    await post.scroll_into_view_if_needed()
                    await self._comment_in_post(page, post, page_id, source_url)
                    return selected["url"]
                await page.mouse.wheel(0, 650)
                await page.wait_for_timeout(1000)
            if attempt < 3:
                await page.wait_for_timeout(20000)
        raise RuntimeError("Đã gửi thao tác đăng nhưng chưa tìm thấy bài mới trong 5 phút gần đây; kiểm tra Page trước khi đăng lại")

    async def _comment_in_post(self, page: Page, post: Locator, page_id: str, source_url: str) -> None:
        editor = post.locator('[contenteditable="true"]').and_(page.locator(":visible"))
        if not await editor.count():
            comment = post.get_by_role("button", name=re.compile(
                r"^(Viết bình luận|Bình luận|Comment|Leave a comment|Write a comment|Komentarz|Skomentuj)$", re.I
            ))
            await comment.first.click()
        await editor.first.wait_for(state="visible", timeout=15000)
        await self._assert_page_identity(page, page_id)
        await editor.first.fill(source_url)
        await editor.first.press("Enter")
        for _ in range(15):
            # Exclude the editor itself when confirming that the comment appeared.
            posted = await post.evaluate('''(root, url) => [...root.querySelectorAll('a[href]')].some(a =>
                !a.closest('[contenteditable="true"]') && (a.textContent.trim() === url || a.href === url))''', source_url)
            if posted:
                return
            await page.wait_for_timeout(1000)
        raise RuntimeError("Đã gửi bình luận nhưng chưa thấy link xuất hiện; kiểm tra bài vừa đăng trước khi thử lại")

    @staticmethod
    def _reel_urls(hrefs: list[str]) -> set[str]:
        urls = set()
        for href in hrefs:
            if "notif" in href.lower():
                continue
            match = re.match(r"https://www\.facebook\.com/reel/(\d+)(?:[/?]|$)", href)
            if match:
                urls.add(f"https://www.facebook.com/reel/{match.group(1)}")
        return urls

    @staticmethod
    def _page_url(page_id: str) -> str:
        return f"https://www.facebook.com/profile.php?id={page_id}"

    @staticmethod
    def _absolute(url: str) -> str:
        if url.startswith("http"):
            return url
        return f"https://www.facebook.com{url}" if url else ""
