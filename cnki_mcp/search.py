"""
CNKI 论文搜索核心逻辑。

从 Selenium 迁移到 Playwright，保留相同的搜索/翻页/排序/弹窗处理策略。
行级解析已抽为纯函数（parsing.parse_paper_row_html），不再依赖脆弱的 CSS 选择器。
"""

import asyncio
import json
import logging
import random
import re
from typing import Any

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from cnki_mcp.config import (
    CNKI_HOME_URL,
    SEARCH_TYPE_VALUES,
    SELECTOR_NEXT_PAGE,
    SELECTOR_RESULT_ROWS,
    SELECTOR_SEARCH_BTN,
    SELECTOR_SEARCH_INPUT,
    SELECTOR_SEARCH_TYPE_DROPDOWN,
    SELECTOR_SEARCH_TYPE_LIST,
    SORT_TYPES,
)
from cnki_mcp.exceptions import SearchError
from cnki_mcp.parsing import filter_papers, parse_paper_row_html
from cnki_mcp.utils import dismiss_popups, resolve_search_type, resolve_sort_type

logger = logging.getLogger("cnki")

# ============ grid API 检索（复用 gxlib-cnki 已验证的参数结构） ============
# 外文检索的关键：Products 中第 11 位 CSCF(中文) → SCSF(外文)，Rlang=CHINESE/FOREIGN
LANGUAGE_CONFIG = {
    "中文": {"rlang": "CHINESE",
            "products": "CJFQ,CAPJ,ZHYX,CJTL,CDFD,CMFD,WBFD,CPFD,IPFD,CCND,CSCF,SCHF,SCSD,SNAD,CCJD,CCVD,CJFN"},
    "外文": {"rlang": "FOREIGN",
            "products": "CJFQ,CAPJ,ZHYX,CJTL,CDFD,CMFD,WBFD,CPFD,IPFD,CCND,SCSF,SCHF,SCSD,SNAD,CCJD,CCVD,CJFN"},
}
LANGUAGE_ALIASES = {"chinese": "中文", "zh": "中文", "cn": "中文",
                    "foreign": "外文", "en": "外文", "english": "外文", "外文": "外文", "英文": "外文"}
CROSSIDS = ("YSTT4HG0,LSTPFY1C,EMRPGLPA,JUP3MUPD,MPMFIG1A,WQ0UVIAA,"
            "BLZOG7CK,PWFIRAGL,NLBO1Z6R,NN3FJMUV")


def resolve_language(language: str) -> str:
    """解析检索语言，支持中英文别名，默认中文"""
    if not language:
        return "中文"
    return LANGUAGE_ALIASES.get(language.lower().strip(), "中文")


async def _search_via_api(
    page: Page,
    query: str,
    search_type: str = "主题",
    language: str = "中文",
    year_from: int | None = None,
    year_to: int | None = None,
    sort: str = "相关度",
    pages: int = 1,
) -> list[dict[str, Any]]:
    """通过 grid API 检索（page.evaluate fetch：同源、带完整 cookie 与浏览器指纹）。

    外文检索走此路径：公开站首页 UI 没有外文入口，但底层 /kns8s/brief/grid
    接口支持 Rlang=FOREIGN + SCSF products（与 gxlib-cnki 包库同构）。
    """
    field = SEARCH_TYPE_VALUES.get(search_type, "SU$%=|").split("$")[0]
    lang_cfg = LANGUAGE_CONFIG.get(language, LANGUAGE_CONFIG["中文"])

    # grid 接口在 kns.cnki.net 域下：先导航过去建立同源上下文，保证相对路径 fetch 生效
    try:
        if "kns.cnki.net" not in page.url:
            await page.goto("https://kns.cnki.net/", wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(random.uniform(1, 2))
    except Exception as e:
        raise SearchError(f"导航到检索服务失败: {e}") from e

    qgroups = [{"Key": "Subject", "Title": "", "Logic": 0,
                "Items": [{"Field": field, "Value": query,
                           "Operator": "TOPRANK" if field == "SU" else "DEFAULT",
                           "Logic": 0, "Title": search_type}],
                "ChildItems": []}]
    if year_from or year_to:
        qgroups[0]["Items"].append({
            "Field": "PT", "Value": str(year_from or ""), "Value2": str(year_to or ""),
            "Operator": "BETWEEN", "Logic": 0, "Title": "发表时间"})
    qj = {
        "Platform": "", "Resource": "CROSSDB", "Classid": "WD0FTY92",
        "Products": lang_cfg["products"],
        "QNode": {"QGroup": qgroups},
        "ExScope": 1, "SimpTrad": "0", "SearchType": 2, "Rlang": lang_cfg["rlang"],
        "Expands": {}, "KuaKuCode": CROSSIDS,
        "View": "changeDBCh", "SearchFrom": 5,
    }
    sort_field = SORT_TYPES.get(sort, "FFD")
    form = {
        "boolSearch": "false",
        "QueryJson": json.dumps(qj, ensure_ascii=False),
        "pageNum": "1", "pageSize": str(pages * 20),
        "sortField": sort_field, "sortType": "desc",
        "dstyle": "listmode", "boolSortSearch": "false",
        "productStr": "", "aside": "",
    }
    js = """async (form) => {
        const fd = new URLSearchParams();
        for (const k in form) fd.append(k, form[k]);
        const r = await fetch('/kns8s/brief/grid', {
            method: 'POST', body: fd,
            headers: {'X-Requested-With': 'XMLHttpRequest'}
        });
        return await r.text();
    }"""
    body = await page.evaluate(js, form)
    if not body or len(body) < 200:
        raise SearchError("grid 检索返回空响应，可能触发反爬拦截，请稍后重试")
    if "/verify/" in body:
        raise SearchError("CNKI 触发了安全验证（滑块），请稍后重试或手动完成验证")

    papers: list[dict[str, Any]] = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    for row_html in rows:
        paper = parse_paper_row_html(f"<tr>{row_html}</tr>")
        if paper.get("title"):
            papers.append(paper)
    return papers


def _cell(row: str, cls: str) -> str:
    """grid 返回行中提取指定 class 单元格文本（兼容 gxlib 解析结构）"""
    m = re.search(r'<td[^>]*class=["\']' + cls + r'["\'][^>]*>(.*?)</td>', row, re.S)
    if not m:
        return ""
    txt = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", txt)).strip()


async def _check_cnki_accessible(page: Page) -> None:
    """检测 CNKI 是否可正常访问，如果被反爬拦截则抛出 SearchError"""
    content = await page.content()
    # HTTP 418 或 CDN 拦截导致空页面
    if len(content) < 200:
        raise SearchError(
            "CNKI 返回了空页面，可能触发了反爬拦截（HTTP 418）。"
            "请检查网络环境，或尝试设置 CNKI_PROXY 环境变量更换代理 IP。"
        )
    # TencentEdgeOne CDN 拦截特征
    lowered = content.lower()
    if "tencentedgeone" in lowered and "set-cookie" in lowered:
        raise SearchError(
            "CNKI 访问被 CDN 拦截（TencentEdgeOne）。"
            "当前 IP 可能被 CNKI 限制了访问，请尝试更换网络环境或使用代理。"
        )
    # 滑块验证码特征（页面标题包含 verify 或出现滑块组件）
    if "verify" in page.url.lower() or ("nc_scale" in lowered and "滑动" in content):
        raise SearchError(
            "CNKI 触发了滑块验证码。请稍后重试，"
            "或先用浏览器手动访问 https://www.cnki.net/ 完成验证。"
        )


async def _goto_home_with_retry(page: Page, retries: int = 2) -> None:
    """导航到 CNKI 首页并做可访问性检查；网络波动或反爬拦截时自动重试。"""
    for attempt in range(retries + 1):
        try:
            await page.goto(CNKI_HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(random.uniform(1, 2))
            await dismiss_popups(page)
            await _check_cnki_accessible(page)
            return
        except SearchError:
            if attempt >= retries:
                raise
            logger.warning("CNKI 首页加载异常，第 %s 次重试", attempt + 1)
            await asyncio.sleep(random.uniform(2, 3))
async def select_search_type(page: Page, search_type: str) -> bool:
    """在 CNKI 首页选择搜索类型（如 主题→关键词）"""
    value = SEARCH_TYPE_VALUES.get(search_type)
    if not value:
        return False
    try:
        await page.locator(SELECTOR_SEARCH_TYPE_DROPDOWN).click()
        await asyncio.sleep(0.8)
        option = page.locator(f'{SELECTOR_SEARCH_TYPE_LIST} a[value="{value}"]')
        if await option.count() > 0:
            await option.first.click()
            await asyncio.sleep(0.5)
            return True
    except Exception:
        pass
    return False


async def apply_sort(page: Page, sort_type: str) -> bool:
    """在搜索结果页面应用排序"""
    sort_id = SORT_TYPES.get(sort_type)
    if not sort_id:
        return False
    try:
        sort_btn = page.locator(f"#{sort_id}")
        if await sort_btn.count() > 0 and await sort_btn.is_visible():
            await sort_btn.click()
            await asyncio.sleep(random.uniform(1.5, 2.5))
            await page.wait_for_selector(SELECTOR_RESULT_ROWS, timeout=15_000)
            return True
    except Exception:
        pass
    return False


async def submit_search(page: Page) -> None:
    """稳健搜索提交：先关联想菜单和弹窗，再 JS 点击或回车"""
    try:
        await page.locator(SELECTOR_SEARCH_INPUT).press("Escape")
        await asyncio.sleep(0.2)
    except Exception:
        pass

    await dismiss_popups(page)
    await asyncio.sleep(0.3)

    try:
        search_btn = page.locator(SELECTOR_SEARCH_BTN).first
        if await search_btn.count() > 0 and await search_btn.is_visible():
            await search_btn.click()
            return
    except Exception:
        pass

    try:
        await page.locator(SELECTOR_SEARCH_INPUT).press("Enter")
    except Exception:
        pass


async def parse_paper_row_async(row) -> dict[str, Any]:
    """从 Playwright 行元素提取论文信息（委托给纯函数解析器）"""
    try:
        html = await row.inner_html()
    except Exception:
        return {"title": "", "url": "", "authors": [], "source": "",
                "date": "", "cited_count": "0", "download_count": "0"}
    return parse_paper_row_html(html)


async def search_cnki_impl(
    page: Page,
    query: str,
    search_type: str = "主题",
    pages: int = 1,
    sort: str = "相关度",
    year_from: int | None = None,
    year_to: int | None = None,
    journals: list[str] | None = None,
    language: str = "中文",
) -> dict[str, Any]:
    """执行 CNKI 搜索并返回结果列表

    year_from/year_to: 按日期列年份做结果侧过滤（取不到年份的论文保留）。
    journals: 期刊名单过滤（大小写不敏感，子串匹配）。
    language: 中文(默认)/外文。外文走 grid API（Rlang=FOREIGN），英文文献。
    """
    resolved_type = resolve_search_type(search_type)
    resolved_sort = resolve_sort_type(sort)
    resolved_lang = resolve_language(language)

    async def _captcha_result() -> dict[str, Any]:
        logger.warning("搜索触发验证码: %s", query)
        return {
            "isError": True,
            "error": "CNKI 触发了验证码（安全验证），请稍后再试或手动完成验证",
            "error_type": "CaptchaError",
            "query": query,
            "search_type": resolved_type,
            "sort": resolved_sort,
            "total_pages": pages,
            "total_papers": 0,
            "papers": [],
        }

    await _goto_home_with_retry(page)

    # 外文检索：公开站 UI 无外文入口，走 grid API 直调
    if resolved_lang != "中文":
        try:
            all_papers = await _search_via_api(
                page, query, resolved_type, resolved_lang,
                year_from=year_from, year_to=year_to,
                sort=resolved_sort, pages=pages,
            )
        except SearchError as e:
            return {"isError": True, "error": str(e), "error_type": "SearchError",
                    "query": query, "papers": []}
        if journals:
            before = len(all_papers)
            all_papers = filter_papers(all_papers, journal_names=journals)
            if len(all_papers) < before:
                logger.info("结果侧过滤: %d -> %d 篇", before, len(all_papers))
        return {
            "query": query,
            "search_type": resolved_type,
            "sort": resolved_sort,
            "language": resolved_lang,
            "total_pages": pages,
            "total_papers": len(all_papers),
            "papers": all_papers,
            "filters": {"year_from": year_from, "year_to": year_to, "journals": journals or []},
        }

    all_papers: list[dict[str, Any]] = []

    if resolved_type != "主题":
        await select_search_type(page, resolved_type)

    search_box = page.locator(SELECTOR_SEARCH_INPUT)
    await search_box.wait_for(timeout=15_000)
    await search_box.fill(query)
    await asyncio.sleep(random.uniform(0.3, 0.6))
    await submit_search(page)
    await asyncio.sleep(random.uniform(3, 5))

    # 检测是否触发验证码（URL 特征优先；跳转有延迟，超时后还会复查一次）
    if "verify" in page.url:
        return await _captcha_result()

    if resolved_sort != "相关度":
        await apply_sort(page, resolved_sort)

    for page_num in range(1, pages + 1):
        try:
            await page.wait_for_selector(SELECTOR_RESULT_ROWS, timeout=15_000)
            rows = page.locator(SELECTOR_RESULT_ROWS)
            count = await rows.count()
            for i in range(count):
                try:
                    row = rows.nth(i)
                    paper = await parse_paper_row_async(row)
                    if paper.get("title"):
                        paper["page"] = page_num
                        all_papers.append(paper)
                except Exception:
                    pass  # 单行解析失败不影响其他行
        except PlaywrightTimeout:
            # 超时复核：可能已跳转到验证码页（跳转有延迟，提交后的即时检查可能漏掉）
            if "verify" in page.url:
                return await _captcha_result()
            pass  # 其余情况说明没有更多结果，正常结束
        except Exception as e:
            logger.warning("搜索页面 %s 解析异常: %s", page_num, e)

        if page_num < pages:
            try:
                next_btn = page.locator(SELECTOR_NEXT_PAGE)
                if await next_btn.count() > 0 and await next_btn.is_enabled():
                    await next_btn.click()
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                else:
                    break
            except Exception:
                break

    before = len(all_papers)
    if year_from is not None or year_to is not None or journals:
        all_papers = filter_papers(
            all_papers, year_from=year_from, year_to=year_to, journal_names=journals,
        )
        if len(all_papers) < before:
            logger.info("结果侧过滤: %d -> %d 篇", before, len(all_papers))

    return {
        "query": query,
        "search_type": resolved_type,
        "sort": resolved_sort,
        "language": resolved_lang,
        "total_pages": pages,
        "total_papers": len(all_papers),
        "papers": all_papers,
        "filters": {
            "year_from": year_from,
            "year_to": year_to,
            "journals": journals or [],
        },
    }
