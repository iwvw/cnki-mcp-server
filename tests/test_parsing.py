"""测试 parsing 模块（纯函数，无需浏览器）"""

from cnki_mcp.parsing import (
    extract_year,
    filter_papers,
    parse_paper_row_html,
    parse_publication_info,
)

# CNKI 2025 前后结果页真实结构（含缩进换行的行 HTML）
ROW_STANDARD = """
<tr>
  <td class="name"><a class="fz14" href="/kcms2/article/abstract?v=abc123&amp;uniplatform=NZKPT" target="_blank">深度学习在医学图像分析中的应用</a></td>
  <td class="author"><a href="/kns8s/defaultresult/index">张三</a>;
    <a href="/kns8s/defaultresult/index">李四</a></td>
  <td class="source"><a href="/knavi/journals/detail" target="_blank">中国医学杂志</a></td>
  <td class="date">2025-05-01</td>
  <td class="data">期刊</td>
  <td class="quote"><a href="#">12</a></td>
  <td class="download"><a href="#">34</a></td>
</tr>
"""

ROW_SINGLE_AUTHOR_NO_LINKS = """
<tr>
  <td class="name"><a class="fz14" href="https://kns.cnki.net/kcms2/article/abstract?v=x" target="_blank">机器学习基础</a></td>
  <td class="author">王五</td>
  <td class="source">经济研究</td>
  <td class="date">2024.03.15</td>
  <td class="quote">被引：8</td>
  <td class="download">下载：56 次</td>
</tr>
"""


def test_parse_standard_row():
    paper = parse_paper_row_html(ROW_STANDARD)
    assert paper["title"] == "深度学习在医学图像分析中的应用"
    # 相对 URL 补全 + &amp; 实体还原
    assert paper["url"] == "https://kns.cnki.net/kcms2/article/abstract?v=abc123&uniplatform=NZKPT"
    assert paper["authors"] == ["张三", "李四"]
    assert paper["source"] == "中国医学杂志"
    assert paper["date"] == "2025-05-01"
    assert paper["cited_count"] == "12"
    assert paper["download_count"] == "34"


def test_parse_row_fallbacks():
    """无链接的 td：来源/被引/下载回退到文本，被引与下载提取数字"""
    paper = parse_paper_row_html(ROW_SINGLE_AUTHOR_NO_LINKS)
    assert paper["title"] == "机器学习基础"
    assert paper["url"] == "https://kns.cnki.net/kcms2/article/abstract?v=x"
    assert paper["authors"] == []
    assert paper["source"] == "经济研究"
    assert paper["date"] == "2024.03.15"
    assert paper["cited_count"] == "8"
    assert paper["download_count"] == "56"


def test_parse_row_empty():
    paper = parse_paper_row_html("")
    assert paper["title"] == ""
    assert paper["authors"] == []
    assert paper["cited_count"] == "0"


def test_extract_year():
    assert extract_year("2025-05-01") == 2025
    assert extract_year("2024.03.15") == 2024
    assert extract_year("2025 年") == 2025
    assert extract_year("") is None
    assert extract_year("暂无日期") is None


def test_filter_papers_year_range():
    papers = [
        {"title": "a", "date": "2023-01-01", "source": "管理世界"},
        {"title": "b", "date": "2024-06-01", "source": "管理世界"},
        {"title": "c", "date": "2025-12-31", "source": "经济研究"},
        {"title": "d", "date": "", "source": "管理世界"},
    ]
    assert [p["title"] for p in filter_papers(papers, year_from=2024)] == ["b", "c", "d"]
    assert [p["title"] for p in filter_papers(papers, year_to=2024)] == ["a", "b", "d"]


def test_filter_papers_journal_names():
    papers = [
        {"title": "a", "source": "管理世界"},
        {"title": "b", "source": "管理世界(半月刊)"},
        {"title": "c", "source": "经济研究"},
    ]
    # 子串匹配 + 大小写不敏感
    assert [p["title"] for p in filter_papers(papers, journal_names=["管理世界"])] == ["a", "b"]
    assert [p["title"] for p in filter_papers(papers, journal_names=["管理世界(半月刊)"])] == ["b"]
    assert [p["title"] for p in filter_papers(papers, journal_names=["MANAGEMENT WORLD"])] == []


def test_parse_publication_info():
    assert parse_publication_info("2025, 45(3): 12-20") == {
        "year": "2025", "volume": "45", "issue": "3", "pages": "12-20",
    }
    assert parse_publication_info("2024(2): 30-45.") == {
        "year": "2024", "volume": "", "issue": "2", "pages": "30-45",
    }
    assert parse_publication_info("2023, 10(1): 1-15") == {
        "year": "2023", "volume": "10", "issue": "1", "pages": "1-15",
    }
    assert parse_publication_info("2025") == {"year": "2025", "volume": "", "issue": "", "pages": ""}
    assert parse_publication_info("") == {"year": "", "volume": "", "issue": "", "pages": ""}