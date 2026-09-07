"""
CNKI 论文搜索核心逻辑。

从 Selenium 迁移到 Playwright，保留相同的搜索/翻页/排序/弹窗处理策略。
行级解析已抽为纯函数（parsing.parse_paper_row_html），不再依赖脆弱的 CSS 选择器。
"""

import asyncio
import logging
import random
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
    if "verify" in page.url.lower() or "nc_scale" in lowered and "滑动" in content:
        raise SearchError(
            "CNKI 触发了滑块验证码。请稍后重试，或先用浏览器手动访问 https://www.cnki.net/ 完成验证。"
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
) -> dict[str, Any]:
    """执行 CNKI 搜索并返回结果列表

    year_from/year_to: 按日期列年份做结果侧过滤（取不到年份的论文保留）。
    journals: 期刊名单过滤（大小写不敏感，子串匹配）。
    """
    resolved_type = resolve_search_type(search_type)
    resolved_sort = resolve_sort_type(sort)
    all_papers: list[dict[str, Any]] = []

    await _goto_home_with_retry(page)

    if resolved_type != "主题":
        await select_search_type(page, resolved_type)

    search_box = page.locator(SELECTOR_SEARCH_INPUT)
    await search_box.wait_for(timeout=15_000)
    await search_box.fill(query)
    await asyncio.sleep(random.uniform(0.3, 0.6))
    await submit_search(page)
    await asyncio.sleep(random.uniform(3, 5))

    # 检测是否触发验证码（URL 特征优先，再做内容级反爬检查）
    if "verify" in page.url:
        logger.warning("搜索触发验证码: %s", query)
        return {
            "isError": True,
            "error": "CNKI 触发了验证码，请稍后再试或手动完成验证",
            "error_type": "CaptchaError",
            "query": query,
            "search_type": resolved_type,
            "sort": resolved_sort,
            "total_pages": pages,
            "total_papers": 0,
            "papers": [],
        }

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
            pass  # 超时说明没有更多结果，正常结束
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
        all_papers = filter_papers(all_papers, year_from=year_from, year_to=year_to, journal_names=journals)
        if len(all_papers) < before:
            logger.info("结果侧过滤: %d -> %d 篇", before, len(all_papers))

    return {
        "query": query,
        "search_type": resolved_type,
        "sort": resolved_sort,
        "total_pages": pages,
        "total_papers": len(all_papers),
        "papers": all_papers,
        "filters": {
            "year_from": year_from,
            "year_to": year_to,
            "journals": journals or [],
        },
    }
