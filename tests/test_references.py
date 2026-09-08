"""测试 references 模块的列表解析（纯逻辑，无需浏览器）"""

from cnki_mcp.references import _extract_file_id, _parse_ref_items

SAMPLE_REFS = """
<ul class="reference-list">
  <li><span class="ref-no">[1]</span>张三. 深度学习在医学图像分析中的应用[J]. 中国医学杂志, 2020, 45(3): 12-20.</li>
  <li><span class="ref-no">[2]</span>李四, 王五. 电商直播研究综述[J]. 商业经济研究, 2023, 78(2): 30-45.</li>
  <li><span class="ref-no">[3]</span>Smith J, Brown K. Live streaming commerce: A review[J]. Journal of Retailing, 2022, 60(1): 1-15.</li>
</ul>
"""


def test_extract_file_id():
    url = "https://kns.cnki.net/kcms2/article/abstract?v=abc123XYZ&uniplatform=NZKPT"
    assert _extract_file_id(url) == "abc123XYZ"
    assert _extract_file_id("https://kns.cnki.net/kcms2/article/abstract?v=xyz") == "xyz"
    assert _extract_file_id("https://kns.cnki.net/no-v-param") == ""


def test_parse_ref_items():
    items = _parse_ref_items(SAMPLE_REFS)
    assert len(items) == 3
    assert items[0]["序号"] == "1"
    assert "深度学习" in items[0]["题名"] or "深度学习" in (items[0]["作者"] + items[0]["来源"])
    assert items[0]["年份"] == "2020"
    assert items[2]["年份"] == "2022"


def test_parse_ref_items_empty():
    assert _parse_ref_items("") == []
    assert _parse_ref_items("<div>no list here</div>") == []