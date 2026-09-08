"""
CNKI 论文详情页解析。

从 Selenium 迁移到 Playwright，提取论文的全部元数据字段。
采用「多候选选择器 + 全文兜底正则」策略：CNKI 详情页结构多变，
任一候选命中即取，全部失败时回退到页面源码正则搜索。
"""

import asyncio
import random
import re
from typing import Any

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from cnki_mcp.exceptions import DetailError
from cnki_mcp.parsing import parse_publication_info

# 机构特征词：作者区混入的机构项会被过滤并归入 institutions
_INSTITUTION_HINTS = ("大学", "学院", "研究院", "研究所", "研究中心", "科学院", "实验室",
                      "公司", "银行", "医院", "中学", "小学", "党校", "支队", "总队", "委员会")

# 标题尾部杂质（如 CNKI 的 "附视频" 标记）
_TITLE_SUFFIX_PATTERN = re.compile(r"\s*(附视频|附音频|附附录|附更正)\s*$")

# 详情页多候选选择器：新版结构变化时依次尝试
_TITLE_SELECTORS = (".wx-tit h1", "h1")
_TITLE_EN_SELECTORS = (".wx-tit h2",)
_AUTHOR_SELECTORS = ("h3.author span a", "h3.author a", ".author span a", "h3.author a[href*='author']")
_ORG_SELECTORS = ("h3.orgn span a", "h3.orgn a", ".orgn span a", "h3.orgn")
_ABSTRACT_SELECTORS = ("#ChDivSummary", ".abstract-text", "div.abstract")
_ABSTRACT_EN_SELECTORS = ("#EnChDivSummary",)
_KEYWORDS_SELECTORS = ("p.keywords a", ".keywords a")
_SOURCE_SELECTORS = ('div.top-tip a[href*="navi.cnki.net"]', 'a[href*="navi.cnki.net"]', ".top-tip a[href*='navi']")
_PUBINFO_SELECTORS = ("div.top-tip span", ".top-tip span", ".journal-info span", ".head-info span")
_DOI_SELECTORS = ("li.top-space", ".top-space", "li:has-text('DOI')")
_CITED_SELECTORS = ("span#refs a", "#refs a", "span#refs")
_DOWNLOAD_SELECTORS = ("span#DownLoadParts a", "#DownLoadParts a", "span#DownLoadParts")
_FUND_SELECTORS = ('li:has-text("基金") p', "p.funds span", "li:has-text('基金')")
_CLASS_SELECTORS = ('li:has-text("分类号") p', "li:has-text('分类号')")

# 全文兜底正则
_RE_YEAR_VOL_PAGES = re.compile(r"(20\d{2})[,，]?\s*(?:(\d+)\s*[\(（]\s*(\d+)\s*[\)）])?\s*[:：]\s*([0-9][0-9\-–~]+)")
_RE_DOI = re.compile(r"DOI\s*[：:]\s*(10\.\d{4,}[^\s<\"']*)", re.IGNORECASE)
_RE_CITED = re.compile(r"被引\s*[：:]?\s*(\d+)")
_RE_DOWNLOAD = re.compile(r"下载(?:量)?\s*[：:]?\s*(\d+)")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(text: str) -> str:
    """去掉 HTML 标签与空白"""
    return _HTML_TAG_RE.sub("", text or "").strip()


async def _first_text(page: Page, selectors: tuple[str, ...]) -> str:
    """依次尝试候选选择器，返回第一个命中的文本（空则 ''）"""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                text = _clean_text(await loc.first.text_content())
                if text:
                    return text
        except Exception:
            continue
    return ""


async def _all_text(page: Page, selectors: tuple[str, ...]) -> list[str]:
    """依次尝试候选选择器，返回第一个命中集合的全部文本"""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            cnt = await loc.count()
            if cnt > 0:
                items = []
                for i in range(cnt):
                    t = _clean_text(await loc.nth(i).text_content())
                    if t:
                        items.append(t)
                if items:
                    return items
        except Exception:
            continue
    return []


async def _page_source(page: Page) -> str:
    """读取页面源码（含动态加载内容）"""
    try:
        return await page.content()
    except Exception:
        return ""


async def get_paper_detail_impl(page: Page, url: str) -> dict[str, Any]:
    """获取论文详情页的全部信息"""
    paper: dict[str, Any] = {
        "url": url,
        "title": "",
        "title_en": "",
        "authors": [],
        "institutions": [],
        "abstract": "",
        "abstract_en": "",
        "keywords": [],
        "keywords_en": [],
        "source": "",
        "year": "",
        "volume": "",
        "issue": "",
        "pages": "",
        "doi": "",
        "cited_count": "",
        "download_count": "",
        "fund": "",
        "classification": "",
    }

    try:
        # 先访问 CNKI 首页建立会话（避免直接访问抽象页触发验证码）
        await page.goto("https://www.cnki.net/", wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(random.uniform(1, 2))
        # 检查是否被反爬拦截
        homepage_content = await page.content()
        if len(homepage_content) < 200:
            raise DetailError(
                "CNKI 返回了空页面，可能触发了反爬拦截（HTTP 418）。"
                "请检查网络环境，或尝试设置 CNKI_PROXY 环境变量更换代理 IP。"
            )

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(random.uniform(1.5, 2.5))
    except PlaywrightTimeout:
        raise DetailError(f"页面加载超时: {url[:80]}")
    except Exception as e:
        raise DetailError(f"页面导航失败: {e}") from e

    source = await _page_source(page)
    # 验证码页面特征：正文过短或含验证组件
    if len(source) < 200 or "verifybox" in source or "安全验证" in source:
        raise DetailError("CNKI 详情页触发了安全验证（滑块），请稍后重试")

    # 标题
    title = await _first_text(page, _TITLE_SELECTORS)
    paper["title"] = _TITLE_SUFFIX_PATTERN.sub("", title).strip()

    # 英文标题
    paper["title_en"] = await _first_text(page, _TITLE_EN_SELECTORS)

    # 作者（可能混入机构：过滤机构关键词归入 institutions）
    authors = await _all_text(page, _AUTHOR_SELECTORS)
    clean_authors, institutions = [], []
    for name in authors:
        if any(hint in name for hint in _INSTITUTION_HINTS):
            institutions.append(name)
        else:
            clean_authors.append(name)
    paper["authors"] = clean_authors

    # 机构
    orgs = await _all_text(page, _ORG_SELECTORS)
    if orgs:
        # h3.orgn 整块文本时按空白/换行拆
        paper["institutions"] = [o for o in orgs if any(hint in o for hint in _INSTITUTION_HINTS) or o]
    paper["institutions"] = list(dict.fromkeys(institutions + paper["institutions"]))

    # 摘要 / 英文摘要
    paper["abstract"] = await _first_text(page, _ABSTRACT_SELECTORS)
    paper["abstract_en"] = await _first_text(page, _ABSTRACT_EN_SELECTORS)

    # 关键词
    keywords = await _all_text(page, _KEYWORDS_SELECTORS)
    paper["keywords"] = [k.rstrip(";；,，") for k in keywords]

    # 来源
    paper["source"] = (await _first_text(page, _SOURCE_SELECTORS)).rstrip(" .")

    # 年/卷/期/页：先候选选择器，再全文兜底
    pub_found = False
    for sel in _PUBINFO_SELECTORS:
        try:
            spans = page.locator(sel)
            cnt = await spans.count()
            for i in range(cnt):
                text = _clean_text(await spans.nth(i).text_content())
                info = parse_publication_info(text)
                if info["year"]:
                    paper["year"], paper["volume"], paper["issue"], paper["pages"] = (
                        info["year"], info["volume"], info["issue"], info["pages"])
                    pub_found = True
                    break
        except Exception:
            continue
        if pub_found:
            break
    if not pub_found:
        m = _RE_YEAR_VOL_PAGES.search(source)
        if m:
            paper["year"] = m.group(1)
            paper["volume"] = m.group(2) or ""
            paper["issue"] = m.group(3) or ""
            paper["pages"] = m.group(4) or ""

    # DOI
    doi = ""
    for sel in _DOI_SELECTORS:
        try:
            loc = page.locator(sel)
            cnt = await loc.count()
            for i in range(cnt):
                text = _clean_text(await loc.nth(i).text_content())
                m = re.search(r"10\.\d{4,}[^\s\"']*", text)
                if m:
                    doi = m.group(0)
                    break
        except Exception:
            continue
        if doi:
            break
    if not doi:
        m = _RE_DOI.search(source)
        if m:
            doi = m.group(1).strip()
    paper["doi"] = doi

    # 被引 / 下载
    cited = await _first_text(page, _CITED_SELECTORS)
    dl = await _first_text(page, _DOWNLOAD_SELECTORS)
    if cited:
        paper["cited_count"] = _clean_text(cited)
    else:
        m = _RE_CITED.search(source)
        if m:
            paper["cited_count"] = m.group(1)
    if dl:
        paper["download_count"] = _clean_text(dl)
    else:
        m = _RE_DOWNLOAD.search(source)
        if m:
            paper["download_count"] = m.group(1)

    # 基金
    paper["fund"] = (await _first_text(page, _FUND_SELECTORS)).rstrip("；;")

    # 分类号
    paper["classification"] = await _first_text(page, _CLASS_SELECTORS)

    return paper
