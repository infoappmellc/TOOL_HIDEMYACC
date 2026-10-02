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
from typing import Any, Awaitable, Callable

import httpx
from playwright.async_api import Locator, Page, async_playwright
from websockets.sync.client import connect as websocket_connect

from .language import article_comment


class BrowserBridge:
    """Connect to a Hidemyacc Chromium profile using the same CDP pattern as GPM."""

    hidemyacc_cdp = "http://127.0.0.1:9333"

    def __init__(self) -> None:
        self.state_file = Path(__file__).resolve().parents[2] / "data" / "browser-bridge.json"

    def status(self, profile_name: str = "") -> dict[str, Any]:
        saved = self._saved_state()
        endpoint = str(saved.get("cdp_url", ""))
        saved_name = str(saved.get("profile_name", ""))
        if endpoint and saved_name and (not profile_name or saved_name == profile_name) and self._is_cdp(endpoint):
            return {"connected": True, "cdp_url": endpoint, "profile_name": saved_name}
        managed = self._managed_connection(profile_name or saved_name)
        if managed and self._is_cdp(managed["cdp_url"]):
            self._save(managed["cdp_url"], managed["profile_name"])
            return {"connected": True, **managed}
        profile_id = self._profile_id(profile_name) if profile_name and self._hidemyacc_page_target() else ""
        running = self._profile_process(profile_id) if profile_id else self._marco_process()
        return {"connected": False, "cdp_url": "", "profile_name": profile_name,
                "browser_running": running is not None}

    def connect_profile(self, profile_name: str) -> dict[str, Any]:
        current = self.status(profile_name)
        if current["connected"]:
            return current
        self._ensure_hidemyacc_control()
        profile_id = self._profile_id(profile_name)
        if not profile_id:
            raise RuntimeError(f"Không tìm thấy profile {profile_name} trong Hidemyacc")
        process = self._profile_process(profile_id)
        if process:
            # Only this profile is unhealthy. Leave every other open profile alone.
            self._close_hidemyacc_profile(profile_name, process[0])
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

    def endpoint(self, profile_name: str = "") -> str:
        status = self.status(profile_name)
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
            raise RuntimeError(
                "Hidemyacc đang mở nhưng chưa bật điều khiển local; hãy mở lại ứng dụng "
                "với cổng 9333. Các profile đang chạy được giữ nguyên."
            )

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
            # The CDP page is a more reliable readiness signal than System
            # Events, which returns a permissions error on Macs where Terminal
            # has not been granted Accessibility access.
            if self._hidemyacc_page_target():
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
  return candidates.find(item => item.name === preferred) || null;
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
        profile_id = self._profile_id(profile_name)
        if not profile_id:
            raise RuntimeError(f"Không tìm thấy profile {profile_name} trong Hidemyacc")
        source = (
            f"return await global.startBrowserProfile({json.dumps(profile_id)}, "
            "undefined, '', undefined);"
        )
        expression = f"window.hidemyacc.app.exec({{sourceCode:{json.dumps(source)}}})"
        result = self._evaluate_hidemyacc(expression, timeout=180)
        return result if isinstance(result, dict) else {"success": False}

    def _profile_id(self, profile_name: str) -> str:
        encoded_name = json.dumps(profile_name)
        return str(self._evaluate_hidemyacc(
            f"window.profiles.find(item => item.name === {encoded_name})?.id || ''"
        ) or "")

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

    @staticmethod
    def _profile_process(profile_id: str) -> tuple[int, list[str]] | None:
        if not profile_id:
            return None
        output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
        marker = f"--user-data-dir="
        for line in output.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or "/Marco.app/Contents/MacOS/Marco" not in parts[1]:
                continue
            try:
                args = shlex.split(parts[1])
            except ValueError:
                continue
            if any(arg.startswith(marker) and Path(arg[len(marker):]).name == f"hma_{profile_id}" for arg in args):
                return int(parts[0]), args
        return None


class FacebookPublisher:
    _COMMENT_LINK_CHECK = r'''(root, url) => {
        const canonical = raw => {
            try {
                let parsed = new URL(raw, location.href);
                if (/(^|\.)facebook\.com$/.test(parsed.hostname) && parsed.pathname === '/l.php') {
                    const target = parsed.searchParams.get('u');
                    if (!target) return '';
                    parsed = new URL(target);
                }
                parsed.searchParams.delete('fbclid');
                parsed.hash = '';
                return parsed.origin + parsed.pathname.replace(/\/$/, '') + parsed.search;
            } catch { return ''; }
        };
        const expected = canonical(url);
        if ([...root.querySelectorAll('a[href]')].some(a =>
            !a.closest('[contenteditable="true"]') &&
            (a.textContent.trim() === url || (expected && canonical(a.href) === expected))
        )) return true;
        // Facebook may keep the URL as plain text instead of linkifying it.
        const compact = value => value.replace(/[\s\u200b]+/g, '');
        const copy = root.cloneNode(true);
        copy.querySelectorAll('[contenteditable="true"]').forEach(el => el.remove());
        const text = copy.textContent || '';
        return compact(text).includes(compact(url)) &&
            !/(đang viết|đang gửi|writing|posting|sending|pisanie|wysyłanie)\s*(?:\.{0,3}|…)/i.test(text);
    }'''

    _COMMENT_PENDING_CHECK = r'''(root, url) => {
        const compact = value => value.replace(/[\s\u200b]+/g, '');
        const copy = root.cloneNode(true);
        copy.querySelectorAll('[contenteditable="true"]').forEach(el => el.remove());
        const text = copy.textContent || '';
        return compact(text).includes(compact(url)) &&
            /(đang viết|đang gửi|writing|posting|sending|pisanie|wysyłanie)\s*(?:\.{0,3}|…)/i.test(text);
    }'''

    def __init__(self, bridge: BrowserBridge) -> None:
        self.bridge = bridge

    async def scan_pages(self, profile_name: str = "") -> list[dict[str, str]]:
        """Discover every Facebook Page available in the active account switcher."""
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(self.bridge.endpoint(profile_name))
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            await self._facebook_home(page)
            cookies = await context.cookies("https://www.facebook.com/")
            personal_id = next((item["value"] for item in cookies if item["name"] == "c_user"), "")
            menu = await self._open_profile_switcher(page)
            raw_names = await menu.locator('button, a[href], [role="button"]').evaluate_all(
                """elements => elements.filter(element => {
                    const box = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden';
                }).map(element => element.getAttribute('aria-label') || element.innerText || '')"""
            )
            candidates: list[str] = []
            for raw_name in raw_names:
                name = self._clean_profile_name(str(raw_name))
                if name and name not in candidates:
                    candidates.append(name)

            found: dict[str, dict[str, str]] = {}
            for name in candidates:
                try:
                    menu = await self._open_profile_switcher(page)
                    row_pattern = re.compile(r"^" + re.escape(name) + r"(?:,|\s|$)", re.I)
                    rows = menu.get_by_role("button", name=row_pattern).or_(
                        menu.get_by_role("link", name=row_pattern)
                    ).and_(page.locator(":visible"))
                    if not await rows.count():
                        continue
                    await rows.first.click(timeout=10000)
                    await page.wait_for_timeout(1800)
                    cookies = await context.cookies("https://www.facebook.com/")
                    actor_id = next(
                        (item["value"] for item in cookies if item["name"] == "i_user"), ""
                    )
                    if actor_id and actor_id != personal_id:
                        found[actor_id] = {
                            "facebook_id": actor_id,
                            "name": name,
                            "url": self._page_url(actor_id),
                        }
                except Exception:
                    # Facebook adds non-profile controls to the same dialog. A
                    # failed candidate must not prevent valid Pages being scanned.
                    continue
            return sorted(found.values(), key=lambda item: item["name"].casefold())

    async def _facebook_home(self, page: Page) -> None:
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)
        if "login" in page.url.lower():
            raise RuntimeError("Facebook chưa đăng nhập trong profile này")

    async def _open_profile_switcher(self, page: Page) -> Locator:
        await self._facebook_home(page)
        # A previous scan may leave the switcher backdrop mounted. Close stale
        # dialogs before reopening the account menu so they cannot intercept the click.
        for _ in range(2):
            if not await page.locator('[role="dialog"]:visible, [role="menu"]:visible').count():
                break
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
        account_pattern = re.compile(
            r"^(Your profile|Account|Trang cá nhân của bạn|Tài khoản|Twoje konto)$", re.I
        )
        account = page.get_by_role("button", name=account_pattern).and_(page.locator(":visible"))
        if not await account.count():
            raise RuntimeError("Không tìm thấy menu tài khoản Facebook")
        await account.first.click(timeout=10000)
        menu = page.locator('[role="dialog"]:visible, [role="menu"]:visible').last
        await menu.wait_for(state="visible", timeout=10000)
        all_profiles = menu.get_by_text(re.compile(
            r"^(See all profiles|See all Pages|Xem tất cả trang cá nhân|Zobacz wszystkie profile)$", re.I
        )).and_(page.locator(":visible"))
        if await all_profiles.count():
            await all_profiles.first.click()
            await page.wait_for_timeout(800)
            menu = page.locator('[role="dialog"]:visible, [role="menu"]:visible').last
            await menu.wait_for(state="visible", timeout=10000)
        return menu

    @staticmethod
    def _clean_profile_name(raw_name: str) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw_name.splitlines() if line.strip()]
        if not lines:
            return ""
        name = re.sub(
            r",?\s*\d+\s+(?:new\s+)?(?:notifications?|thông báo|powiadomienia?)$",
            "", lines[0], flags=re.I,
        ).strip(" ,")
        name = re.sub(
            r",?\s*(?:currently selected|selected|đang chọn|aktualnie wybrano)$",
            "", name, flags=re.I,
        ).strip(" ,")
        blocked = re.compile(
            r"^(see all|xem tất cả|zobacz wszystkie|create|tạo|utwórz|search|tìm|szukaj|"
            r"log out|đăng xuất|wyloguj|settings|cài đặt|ustawienia|help|trợ giúp|pomoc|"
            r"privacy|quyền riêng tư|feedback|phản hồi|meta verified|cancel|hủy|anuluj)(?:\b|$)",
            re.I,
        )
        return "" if len(name) < 2 or len(name) > 120 or blocked.search(name) else name

    async def publish(
        self,
        page_id: str,
        page_name: str,
        video_path: Path,
        source_url: str,
        title: str = "",
        article_language: str = "",
        dry_run: bool = True,
        profile_name: str = "",
        before_post: Callable[[], Awaitable[None]] | None = None,
        progress: Callable[[str, str, str], None] | None = None,
    ) -> dict[str, str]:
        if not title.strip():
            raise ValueError("Chưa có tiêu đề bài báo; hãy chạy lại Page để lấy tiêu đề")
        report = lambda step, message, url="": progress(step, message, url) if progress else None
        report("browser", "Đang kết nối Facebook trong browser profile")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(self.bridge.endpoint(profile_name))
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            report("switch_page", f"Đang chuyển sang đúng Page {page_name}")
            await self._switch_page(page, page_id, page_name)
            report("switch_done", "Đã xác nhận danh tính Page")
            report("snapshot", "Đang ghi nhận bài cũ để tránh bình luận nhầm")
            previous_posts = {item["key"] for item in await self._feed_posts(page, page_id, title)} if not dry_run else set()
            screenshot, post_url = await self._upload_reel(
                page, page_id, page_name, video_path, dry_run, title, before_post, progress
            )
            if dry_run:
                return {"preview": str(screenshot), "post_url": ""}
            report("post_wait", "Đã bấm đăng; bắt đầu quét tiêu đề trên Page")
            await page.wait_for_timeout(3000)
            post_url = await self._comment_on_recent_post(
                page, page_id, source_url, previous_posts, title, progress,
                article_language=article_language,
            )
            report("comment_done", "Đã xác nhận bình luận link nguồn trên đúng bài", post_url)
            return {"preview": str(screenshot), "post_url": post_url}

    async def comment_existing(
        self, profile_name: str, page_id: str, page_name: str, source_url: str, title: str,
        article_language: str = "",
        progress: Callable[[str, str, str], None] | None = None,
    ) -> str:
        """Resume a verified/possibly submitted Reel without uploading it again."""
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(self.bridge.endpoint(profile_name))
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            await self._switch_page(page, page_id, page_name)
            post_url = await self._comment_on_recent_post(
                page, page_id, source_url, set(), title, progress,
                article_language=article_language,
            )
            if progress:
                progress("comment_done", "Đã xác nhận bình luận link nguồn trên đúng bài", post_url)
            return post_url

    async def _assert_page_identity(self, page: Page, page_id: str) -> None:
        cookies = await page.context.cookies("https://www.facebook.com/")
        actor_id = next((item["value"] for item in cookies if item["name"] == "i_user"), "")
        if actor_id != page_id:
            raise RuntimeError(f"Facebook chưa hoạt động dưới đúng Page ID {page_id}; đã dừng trước khi đăng")

    async def _switch_page(self, page: Page, page_id: str, page_name: str) -> None:
        # Follow TOOL-GPM: home -> account menu -> profile list -> exact Page row.
        await self._facebook_home(page)
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
        self, page: Page, page_id: str, page_name: str, video_path: Path, dry_run: bool,
        title: str = "", before_post: Callable[[], Awaitable[None]] | None = None,
        progress: Callable[[str, str, str], None] | None = None,
    ) -> tuple[Path, str]:
        report = lambda step, message, url="": progress(step, message, url) if progress else None
        report("composer", "Đang mở trình tạo Reel")
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
        report("upload", "Đang tải video lên Facebook")
        await file_input.last.set_input_files(str(video_path))
        await page.wait_for_timeout(5000)
        report("processing", "Đang chờ Facebook xử lý video")

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

        # The article title identifies the Reel; the source URL belongs in its comment.
        description_pattern = re.compile(r"describe your reel|mô tả thước phim|opisz.*rolk", re.I)
        descriptions = page.get_by_role("textbox", name=description_pattern).or_(
            page.get_by_placeholder(description_pattern)
        ).and_(page.locator(":visible"))
        if not await descriptions.count():
            # Facebook's Lexical editor uses aria-placeholder (not placeholder or
            # an accessible textbox name) on the Reel settings screen.
            editors = page.locator(
                '[role="form"] [contenteditable="true"][role="textbox"][aria-placeholder]'
            ).and_(page.locator(":visible"))
            for index in range(await editors.count()):
                editor = editors.nth(index)
                if description_pattern.search(await editor.get_attribute("aria-placeholder") or ""):
                    descriptions = editor
                    break
        if not await descriptions.count():
            raise RuntimeError("Không tìm thấy ô status Reel để điền tiêu đề bài báo")
        await descriptions.first.fill(title)
        written = await descriptions.first.evaluate('(element) => element.value ?? element.innerText')
        if " ".join(written.split()) != " ".join(title.split()):
            raise RuntimeError("Đã nhập nhưng Facebook chưa ghi nhận tiêu đề trong ô mô tả Reel")
        report("caption", "Đã điền status bằng tiêu đề bài báo")
        await self._assert_page_identity(page, page_id)
        preview = video_path.parent / "facebook-preview.png"
        await page.screenshot(path=str(preview), full_page=False)
        if dry_run:
            return preview, ""
        if before_post:
            await before_post()
            await self._assert_page_identity(page, page_id)
        report("post", "Đang bấm đăng Reel")
        await button.click()
        # Notification links can point to unrelated or old Reels.
        match = re.match(r"https://www\.facebook\.com/reel/(\d+)(?:[/?]|$)", page.url)
        return preview, f"https://www.facebook.com/reel/{match.group(1)}" if match else ""

    async def _feed_posts(self, page: Page, page_id: str, title: str = "") -> list[dict[str, Any]]:
        return await page.evaluate(r'''({pageId, title}) => {
            const normalized = value => (value || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
            const signature = normalized(title).slice(0, 60).trim();
            const minutes = text => {
                text = text.replace(/\u00a0/g, ' ').trim().toLocaleLowerCase();
                if (/(vừa xong|vừa mới|just now|a moment ago|przed chwilą)/.test(text)) return 0;
                if (/(?:^|[\s·])\d+\s*(giây( trước)?|seconds?( ago)?|s|sek\.?)(?=$|[\s·])/.test(text)) return 0;
                const match = text.match(/(?:^|[\s·])(\d+)\s*(phút( trước)?|minutes?( ago)?|mins?( ago)?|m|min\.?|minut[ay]? temu)(?=$|[\s·])/);
                return match ? Number(match[1]) : null;
            };
            document.querySelectorAll('[data-hma-feed-post]').forEach(el =>
                el.removeAttribute('data-hma-feed-post'));
            const posts = [], seen = new Set(), seenKeys = new Set();
            // Facebook may mount the caption before the video or Comment button.
            // Include visible-title nodes and inspect candidates in feed order.
            const captions = signature ? [...document.querySelectorAll('p,span,div')].filter(el =>
                !el.children.length && normalized(el.innerText).includes(signature)) : [];
            const seeds = [...document.querySelectorAll('video'), ...captions].sort((a, b) =>
                a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
            for (const seed of seeds) {
                let post = seed.parentElement;
                for (let depth = 0; post && depth < 35; depth++, post = post.parentElement) {
                    if (post === document.body || post.getAttribute('role') === 'feed' || post.getAttribute('role') === 'main') break;
                    // Do not climb into a wrapper containing multiple feed posts.
                    if (post.querySelectorAll('video').length > 1) break;
                    const links = [...post.querySelectorAll('a[href]')];
                    const author = links.find(a => {
                        try { return new URL(a.href).searchParams.has('id'); } catch { return false; }
                    });
                    if (!author || new URL(author.href).searchParams.get('id') !== pageId) continue;
                    if (seen.has(post)) break;
                    const lines = post.innerText.split('\n').map(x => x.trim()).filter(Boolean);
                    const time = lines.slice(0, 60).find(x => minutes(x) !== null);
                    seen.add(post);
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
                    if (seenKeys.has(key)) break;
                    seenKeys.add(key);
                    const index = posts.length;
                    post.setAttribute('data-hma-feed-post', String(index));
                    posts.push({key, url, text: post.innerText, minutes: time ? minutes(time) : null, index});
                    break;
                }
            }
            return posts;
        }''', {"pageId": page_id, "title": title})

    @staticmethod
    def _select_recent_post(
        posts: list[dict[str, Any]], previous_posts: set[str], title: str,
    ) -> dict[str, Any] | None:
        expected = " ".join(title.casefold().split())
        if not expected:
            raise ValueError("Thiếu tiêu đề bài báo để nhận diện bài vừa đăng")
        # Facebook may collapse long Reel descriptions in the Page feed.
        signature = expected if len(expected) <= 60 else expected[:60].rstrip()
        return next((item for item in posts if item["key"] not in previous_posts
                     and signature in " ".join(item.get("text", "").casefold().split())), None)

    async def _comment_on_recent_post(
        self, page: Page, page_id: str, source_url: str, previous_posts: set[str], title: str,
        progress: Callable[[str, str, str], None] | None = None,
        article_language: str = "",
    ) -> str:
        report = lambda step, message, url="": progress(step, message, url) if progress else None
        for attempt in range(5):
            report("find_post", f"Đang quét bài có tiêu đề khớp (lần {attempt + 1}/5)")
            await page.goto(self._page_url(page_id), wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(3500)
            await self._assert_page_identity(page, page_id)
            for position in range(16):
                posts = await self._feed_posts(page, page_id, title)
                selected = self._select_recent_post(posts, previous_posts, title)
                if selected:
                    report("post_found", "Đã gặp bài có tiêu đề khớp trên đúng Page", selected["url"])
                    post = page.locator(f'[data-hma-feed-post="{selected["index"]}"]')
                    await post.scroll_into_view_if_needed()
                    report("comment", "Đang bình luận link bài báo")
                    await self._comment_in_post(page, post, page_id, source_url, title,
                                                article_language, progress)
                    match = re.match(r"https://www\.facebook\.com/reel/(\d+)(?:[/?]|$)", page.url)
                    return selected["url"] or (f"https://www.facebook.com/reel/{match.group(1)}" if match else "")
                if position in {0, 5, 10, 15}:
                    report("scan", f"Đã quét {len(posts)} bài ở vị trí {position + 1}/16; đang tìm đúng tiêu đề")
                if position < 15:
                    await page.mouse.wheel(0, 260)
                    await page.wait_for_timeout(1800)
            if attempt < 4:
                await page.wait_for_timeout(15000)
        raise RuntimeError("Đã đăng nhưng chưa tìm thấy bài có đúng tiêu đề trên Page; kiểm tra bài đã đăng trước khi chạy lại")

    async def _comment_in_post(
        self, page: Page, post: Locator, page_id: str, source_url: str, title: str = "",
        article_language: str = "",
        progress: Callable[[str, str, str], None] | None = None,
    ) -> None:
        def verification_root() -> Locator:
            # On a dedicated Reel, Facebook renders comments beside the post,
            # outside the feed-post element. The URL identifies a single Reel.
            if re.match(r"https://www\.facebook\.com/reel/\d+(?:[/?]|$)", page.url):
                return page.locator("body")
            return post

        async def wait_for_existing() -> bool:
            for attempt in range(30):
                root = verification_root()
                if not await root.evaluate(self._COMMENT_PENDING_CHECK, source_url):
                    return await root.evaluate(self._COMMENT_LINK_CHECK, source_url)
                if progress and attempt % 5 == 0:
                    progress("comment_wait", "Facebook đang xử lý bình luận; chờ link hiển thị", "")
                await page.wait_for_timeout(2000)
            raise RuntimeError("Facebook vẫn đang xử lý bình luận; chưa gửi thêm để tránh comment trùng")

        if await wait_for_existing():
            return
        editor = post.locator('[contenteditable="true"]').and_(page.locator(":visible"))
        if not await editor.count():
            comment = post.get_by_role("button", name=re.compile(
                r"^(Viết bình luận|Bình luận|Comment|Leave a comment|Write a comment|Komentarz|Skomentuj)$", re.I
            ))
            await comment.first.click()
            await page.wait_for_timeout(800)
            # Facebook can open a Reel route and re-render the entire post after
            # clicking Comment. Re-identify it by title before touching the editor.
            if title:
                refreshed = self._select_recent_post(await self._feed_posts(page, page_id, title), set(), title)
                if not refreshed:
                    raise RuntimeError("Facebook đã đổi màn hình khi mở bình luận; chưa xác nhận lại được đúng Reel")
                post = page.locator(f'[data-hma-feed-post="{refreshed["index"]}"]')
                if await wait_for_existing():
                    return
            editor = post.locator('[contenteditable="true"]').and_(page.locator(":visible"))
            if not await editor.count() and re.match(r"https://www\.facebook\.com/reel/\d+", page.url):
                reel_editors = page.get_by_role("textbox", name=re.compile(
                    r"Bình luận dưới tên|Comment as|Write a comment|Komentuj", re.I
                )).and_(page.locator(':visible'))
                if await reel_editors.count() == 1:
                    editor = reel_editors
        await editor.first.wait_for(state="visible", timeout=15000)
        await self._assert_page_identity(page, page_id)
        comment_text = article_comment(source_url, title, article_language)
        await editor.first.fill(comment_text)
        await page.wait_for_timeout(1500)
        written = await editor.first.evaluate('(element) => element.value ?? element.innerText')
        if " ".join(written.split()) != " ".join(comment_text.split()):
            raise RuntimeError("Facebook chưa ghi nhận đủ nội dung bình luận; chưa bấm gửi")
        await editor.first.press("Enter")
        await page.wait_for_timeout(4000)
        for attempt in range(30):
            root = verification_root()
            if not await root.evaluate(self._COMMENT_PENDING_CHECK, source_url) and \
                    await root.evaluate(self._COMMENT_LINK_CHECK, source_url):
                return
            if progress and attempt % 5 == 0:
                progress("comment_wait", "Đã gửi comment; đang chờ Facebook hiển thị và xác nhận link", "")
            await page.wait_for_timeout(2000)
        raise RuntimeError("Đã gửi comment nhưng Facebook chưa xác nhận sau hơn 60 giây; kiểm tra lịch sử trước khi thử lại")

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
