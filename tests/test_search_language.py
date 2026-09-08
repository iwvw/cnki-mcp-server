"""测试 search 模块的外文检索参数构造与语言解析（纯逻辑，无需浏览器）"""

from cnki_mcp.search import (
    LANGUAGE_CONFIG,
    CROSSIDS,
    resolve_language,
)


def test_language_config_foreign():
    """外文与中文的差异点：Rlang 值 + products 第 11 位 CSCF→SCSF"""
    zh = LANGUAGE_CONFIG["中文"]
    en = LANGUAGE_CONFIG["外文"]
    assert zh["rlang"] == "CHINESE"
    assert en["rlang"] == "FOREIGN"
    zh_products = zh["products"].split(",")
    en_products = en["products"].split(",")
    assert len(zh_products) == len(en_products)
    assert zh_products[10] == "CSCF"
    assert en_products[10] == "SCSF"
    # 其余位一致
    for i, (a, b) in enumerate(zip(zh_products, en_products)):
        if i != 10:
            assert a == b


def test_resolve_language():
    assert resolve_language("") == "中文"
    assert resolve_language("中文") == "中文"
    assert resolve_language("外文") == "外文"
    assert resolve_language("英文") == "外文"
    assert resolve_language("EN") == "外文"
    assert resolve_language("English") == "外文"
    assert resolve_language("cn") == "中文"


def test_crossids_present():
    assert len(CROSSIDS.split(",")) >= 8
    assert "YSTT4HG0" in CROSSIDS