from unittest.mock import Mock

from apps.dashboard.publisher import BrowserBridge


def test_connect_keeps_other_profile_open(monkeypatch):
    bridge = BrowserBridge()
    monkeypatch.setattr(bridge, "_ensure_hidemyacc_control", Mock())
    monkeypatch.setattr(bridge, "status", Mock(return_value={"connected": False}))
    monkeypatch.setattr(bridge, "_profile_id", Mock(return_value="target-id"))
    monkeypatch.setattr(bridge, "_profile_process", Mock(return_value=None))
    close = Mock()
    monkeypatch.setattr(bridge, "_close_hidemyacc_profile", close)
    monkeypatch.setattr(bridge, "_start_hidemyacc_profile", Mock(return_value={"success": True, "port": 20577}))
    monkeypatch.setattr(bridge, "_is_cdp", Mock(return_value=True))
    monkeypatch.setattr(bridge, "_save", Mock())

    result = bridge.connect_profile("FB_03")

    assert result["profile_name"] == "FB_03"
    bridge._profile_process.assert_called_once_with("target-id")
    close.assert_not_called()


def test_connect_reuses_healthy_target(monkeypatch):
    bridge = BrowserBridge()
    monkeypatch.setattr(bridge, "_ensure_hidemyacc_control", Mock())
    monkeypatch.setattr(bridge, "status", Mock(return_value={
        "connected": True, "profile_name": "FB_02", "cdp_url": "http://127.0.0.1:20002"
    }))
    start = Mock()
    close = Mock()
    monkeypatch.setattr(bridge, "_start_hidemyacc_profile", start)
    monkeypatch.setattr(bridge, "_close_hidemyacc_profile", close)

    assert bridge.connect_profile("FB_02")["cdp_url"] == "http://127.0.0.1:20002"
    start.assert_not_called()
    close.assert_not_called()


def test_connect_recovers_only_broken_target(monkeypatch):
    bridge = BrowserBridge()
    monkeypatch.setattr(bridge, "_ensure_hidemyacc_control", Mock())
    monkeypatch.setattr(bridge, "status", Mock(return_value={"connected": False}))
    monkeypatch.setattr(bridge, "_profile_id", Mock(return_value="target-id"))
    monkeypatch.setattr(bridge, "_profile_process", Mock(return_value=(1234, [])))
    close = Mock()
    monkeypatch.setattr(bridge, "_close_hidemyacc_profile", close)
    monkeypatch.setattr(bridge, "_start_hidemyacc_profile", Mock(return_value={"success": True, "port": 20577}))
    monkeypatch.setattr(bridge, "_is_cdp", Mock(return_value=True))
    monkeypatch.setattr(bridge, "_save", Mock())

    bridge.connect_profile("FB_03")

    close.assert_called_once_with("FB_03", 1234)


def test_status_does_not_report_another_profile(monkeypatch):
    bridge = BrowserBridge()
    monkeypatch.setattr(bridge, "_saved_state", Mock(return_value={
        "profile_name": "FB_02", "cdp_url": "http://127.0.0.1:20002"
    }))
    monkeypatch.setattr(bridge, "_managed_connection", Mock(return_value=None))
    monkeypatch.setattr(bridge, "_hidemyacc_page_target", Mock(return_value=""))
    monkeypatch.setattr(bridge, "_marco_process", Mock(return_value=None))
    monkeypatch.setattr(bridge, "_is_cdp", Mock(return_value=True))

    assert bridge.status("FB_03")["connected"] is False
    bridge._managed_connection.assert_called_once_with("FB_03")
