"""测试 detail 模块的纯逻辑部分（清洗/作者机构分离/出版信息正则，无需浏览器）"""


from cnki_mcp.detail import (
    _INSTITUTION_HINTS,
    _RE_CITED,
    _RE_DOI,
    _RE_DOWNLOAD,
    _RE_YEAR_VOL_PAGES,
    _TITLE_SUFFIX_PATTERN,
    _clean_text,
)


def test_clean_text_strips_html():
    assert _clean_text("  摘要内容  ") == "摘要内容"
    assert _clean_text("<p>hello</p><br>world") == "helloworld"
    assert _clean_text("") == ""


def test_title_suffix_removed():
    assert _TITLE_SUFFIX_PATTERN.sub("", "电商直播研究 附视频") == "电商直播研究"
    assert _TITLE_SUFFIX_PATTERN.sub("", "普通标题") == "普通标题"


def test_author_institution_separation():
    authors = ["师雪薇", "田晓", "河北工程技术学院商学院"]
    clean, inst = [], []
    for name in authors:
        if any(hint in name for hint in _INSTITUTION_HINTS):
            inst.append(name)
        else:
            clean.append(name)
    assert clean == ["师雪薇", "田晓"]
    assert inst == ["河北工程技术学院商学院"]


def test_year_vol_pages_regex():
    m = _RE_YEAR_VOL_PAGES.search("2026, 45(3): 12-20")
    assert m.group(1) == "2026"
    assert m.group(2) == "45"
    assert m.group(3) == "3"
    assert m.group(4) == "12-20"

    m = _RE_YEAR_VOL_PAGES.search("2026: 12-20")
    assert m.group(1) == "2026"
    assert m.group(2) is None

    # 中文冒号
    m = _RE_YEAR_VOL_PAGES.search("2025，10（2）：30-45")
    assert m.group(1) == "2025"
    assert m.group(4) == "30-45"


def test_doi_regex():
    m = _RE_DOI.search("DOI：10.1234/j.cnki.2026.01.001")
    assert m.group(1) == "10.1234/j.cnki.2026.01.001"


def test_cited_download_regex():
    assert _RE_CITED.search("被引：8").group(1) == "8"
    assert _RE_CITED.search("被引 8").group(1) == "8"
    assert _RE_DOWNLOAD.search("下载：56").group(1) == "56"
    assert _RE_DOWNLOAD.search("下载量 56 次").group(1) == "56"