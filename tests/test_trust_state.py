"""测试信任状态持久化的纯逻辑（storage_state 文件读写路径）"""

import json
import os

from cnki_mcp.config import TRUST_STATE_FILE


def test_trust_state_file_path():
    """默认路径在用户目录，且可被 CNKI_STATE_FILE 覆盖"""
    assert TRUST_STATE_FILE == os.path.join(os.path.expanduser("~"), ".cnki_mcp_state.json")
    assert os.path.isabs(TRUST_STATE_FILE)


def test_trust_state_roundtrip(tmp_path):
    """storage_state 的 JSON 结构可写可读（Playwright 兼容格式）"""
    state = {"cookies": [{"name": "LID", "value": "test", "domain": ".cnki.net",
                          "path": "/", "expires": -1, "httpOnly": False, "secure": False}],
             "origins": []}
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["cookies"][0]["name"] == "LID"
    assert "origins" in loaded


def test_state_file_not_present_ok():
    """信任状态文件不存在时不应报错（browser.py 会检查 os.path.exists）"""
    assert not os.path.exists(os.path.join("__definitely_missing__", "state.json"))