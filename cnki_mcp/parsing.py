"""CNKI 网页解析纯函数模块。

把「搜索结果行」「出版信息」的解析从 Playwright 中抽离出来，
只需要原始 HTML 字符串即可工作，不依赖浏览器，方便单元测试与复用。

- parse_paper_row_html: 解析搜索结果表格中的一行 <tr>
- extract_year / filter_papers: 年份与期刊名单过滤
- parse_publication_info: 解析详情页 "年, 卷(期): 页" 文本
"""

import re
from html.parser import HTMLParser
from typing import Any

BASE_URL = "https://kns.cnki.net"

_ROLE_TITLE = "title"
_ROLE_AUTHOR = "author"
_ROLE_SOURCE = "source"
_ROLE_QUOTE = "quote"
_ROLE_DOWNLOAD = "download"
_ROLE_IGNORE = "ignore"


def _extract_number(text: str) -> str:
    """从任意文本中提取第一个数字串，取不到返回 "0"（被引/下载计数）。"""
    m = re.search(r"\d+", text or "")
    return m.group(0) if m else "0"


def _absolute_url(href: str) -> str:
    """CNKI 结果页链接常为相对路径，补全为 https://kns.cnki.net/..."""
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        return "https:" + href
    return BASE_URL + (href if href.startswith("/") else "/" + href)


class _ResultRowParser(HTMLParser):
    """解析 CNKI 搜索结果表格的单行 <tr> HTML。

    兼容旧版选择器对应的真实结构：
    - 标题：行内任意位置 class 含 fz14 的 <a>
    - 作者：td.author 内的多个 <a>
    - 来源：td.source 内第一个 <a>（无链接时回退到 td 文本）
    - 日期：td.date 文本
    - 被引：td.quote 内第一个 <a>（无链接时回退到 td 文本）→ 提取数字
    - 下载：td.download 内第一个 <a>（无链接时回退到 td 文本）→ 提取数字
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paper: dict[str, Any] = {
            "title": "",
            "url": "",
            "authors": [],
            "source": "",
            "date": "",
            "cited_count": "0",
            "download_count": "0",
        }
        self._td_classes: list[list[str]] = []
        self._a_roles: list[str] = []
        self._buf: list[str] = []
        self._seen_title = False
        self._seen_quote_link = False
        self._seen_download_link = False

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> list[str]:
        for key, value in attrs:
            if key == "class" and value:
                return value.split()
        return []

    @staticmethod
    def _attrs_map(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: (value or "") for key, value in attrs}

    def _emit(self, role: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if role == _ROLE_TITLE and not self.paper["title"]:
            self.paper["title"] = text
        elif role == _ROLE_AUTHOR:
            self.paper["authors"].append(text)
        elif role == _ROLE_SOURCE and not self.paper["source"]:
            self.paper["source"] = text
        elif role == _ROLE_QUOTE and not self._seen_quote_link:
            self._seen_quote_link = True
            self.paper["cited_count"] = _extract_number(text)
        elif role == _ROLE_DOWNLOAD and not self._seen_download_link:
            self._seen_download_link = True
            self.paper["download_count"] = _extract_number(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "td":
            self._td_classes.append(self._classes(attrs))
            return
        if tag != "a":
            return
        attrs_map = self._attrs_map(attrs)
        classes = attrs_map.get("class", "").split()
        td_cls = self._td_classes[-1] if self._td_classes else []

        if "fz14" in classes and not self._seen_title:
            self._seen_title = True
            self._a_roles.append(_ROLE_TITLE)
            self.paper["url"] = _absolute_url(attrs_map.get("href", ""))
        elif "author" in td_cls:
            self._a_roles.append(_ROLE_AUTHOR)
        elif "source" in td_cls:
            self._a_roles.append(_ROLE_SOURCE)
        elif "quote" in td_cls:
            self._a_roles.append(_ROLE_QUOTE)
        elif "download" in td_cls:
            self._a_roles.append(_ROLE_DOWNLOAD)
        else:
            self._a_roles.append(_ROLE_IGNORE)
        self._buf = []

    def handle_data(self, data: str) -> None:
        self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            if self._a_roles:
                role = self._a_roles.pop()
                text = "".join(self._buf).strip()
                self._buf = []
                self._emit(role, text)
            return
        if tag == "td":
            td_cls = self._td_classes.pop() if self._td_classes else []
            text = "".join(self._buf).strip()
            self._buf = []
            if "date" in td_cls and not self.paper["date"] and text:
                self.paper["date"] = text
            elif "source" in td_cls and not self.paper["source"] and text:
                self.paper["source"] = text
            elif "quote" in td_cls and not self._seen_quote_link and text:
                self._seen_quote_link = True
                self.paper["cited_count"] = _extract_number(text)
            elif "download" in td_cls and not self._seen_download_link and text:
                self._seen_download_link = True
                self.paper["download_count"] = _extract_number(text)


def parse_paper_row_html(html: str) -> dict[str, Any]:
    """解析搜索结果表格中的一行 HTML，返回论文字段 dict。"""
    parser = _ResultRowParser()
    try:
        parser.feed(html)
    except Exception:
        # 解析异常不致命，返回已收集的部分字段
        pass
    return parser.paper


def extract_year(text: str) -> int | None:
    """从日期文本（如 2025-05-01 / 2025.03 / 2025 年）提取年份，取不到返回 None。"""
    m = re.search(r"(?:19|20)\d{2}", text or "")
    return int(m.group(0)) if m else None


def filter_papers(
    papers: list[dict],
    year_from: int | None = None,
    year_to: int | None = None,
    journal_names: list[str] | None = None,
) -> list[dict]:
    """按年份范围与期刊名单过滤论文列表（结果侧过滤，大小写不敏感）。

    - year_from/year_to: 对 papers 中 date 字段提取的年份做区间过滤，取不到年份的论文在开启区间时保留。
    - journal_names: 期刊名匹配采用「名单条目是来源的子串」规则（如传「管理世界」可匹配「管理世界(半月刊)」）。
    """
    result: list[dict] = []
    names = [n.strip().lower() for n in (journal_names or []) if n and n.strip()]
    for paper in papers:
        year = extract_year(paper.get("date", ""))
        if year_from is not None and year is not None and year < year_from:
            continue
        if year_to is not None and year is not None and year > year_to:
            continue
        if names:
            source = (paper.get("source") or "").strip().lower()
            if not any(name and name in source for name in names):
                continue
        result.append(paper)
    return result


def parse_publication_info(text: str) -> dict[str, str]:
    """解析详情页出版信息文本，如 "2025, 45(3): 12-20" / "2025(3): 12-20" / "2025: 12-20"。

    返回 year / volume / issue / pages 四字段（取不到为空字符串）。
    """
    result = {"year": "", "volume": "", "issue": "", "pages": ""}
    text = (text or "").strip()
    if not text:
        return result

    year_m = re.search(r"(?:19|20)\d{2}", text)
    if not year_m:
        return result
    result["year"] = year_m.group(0)
    rest = text[year_m.end():].strip().lstrip(",.， ")

    # 卷(期)：如 "45(3)" 或 "(3)"（无卷）
    vol_issue = re.search(r"(\d+)?\s*\((\d+)\)", rest)
    if vol_issue:
        if vol_issue.group(1):
            result["volume"] = vol_issue.group(1)
        result["issue"] = vol_issue.group(2)
        rest = rest[vol_issue.end():].strip()

    # 页码：冒号后内容
    pages_m = re.search(r"[:：]\s*(\S+)", rest)
    if pages_m:
        result["pages"] = pages_m.group(1).rstrip(".。")
    return result