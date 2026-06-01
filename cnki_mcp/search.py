"""
CNKI 论文搜索核心逻辑。

从 Selenium 迁移到 Playwright，保留相同的搜索/翻页/排序/弹窗处理策略。
"""

import asyncio
import random
from typing import Any

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from cnki_mcp.config import (
    SEARCH_TYPE_VALUES,
    SELECTOR_RESULT_ROWS,
    SELECTOR_SEARCH_BTN,
    SELECTOR_SEARCH_INPUT,
    SELECTOR_SEARCH_TYPE_DROPDOWN,
    SELECTOR_SEARCH_TYPE_LIST,
    SELECTOR_NEXT_PAGE,
    SORT_TYPES,
)
from cnki_mcp.exceptions import SearchError
from cnki_mcp.utils import dismiss_popups, resolve_search_type, resolve_sort_type


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
    if "TencentEdgeOne" in content and "set-cookie" in content.lower():
        raise SearchError(
            "CNKI 访问被 CDN 拦截（TencentEdgeOne）。"
            "当前 IP 可能被 CNKI 限制了访问，请尝试更换网络环境或使用代理。"
        )
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
    """从 Playwright Locator 元素中提取论文信息"""
    paper: dict[str, Any] = {}

    try:
        title_el = row.locator("a.fz14").first
        if await title_el.count() > 0:
            paper["title"] = (await title_el.text_content() or "").strip()
            paper["url"] = (await title_el.get_attribute("href")) or ""
        else:
            paper["title"] = ""
            paper["url"] = ""
    except Exception:
        paper["title"] = ""
        paper["url"] = ""

    try:
        author_els = await row.locator("td.author a").all()
        paper["authors"] = [(await a.text_content() or "").strip() for a in author_els]
    except Exception:
        paper["authors"] = []

    try:
        source_el = row.locator("td.source a").first
        paper["source"] = ((await source_el.text_content()) or "").strip() if await source_el.count() > 0 else ""
    except Exception:
        paper["source"] = ""

    try:
        date_el = row.locator("td.date").first
        paper["date"] = ((await date_el.text_content()) or "").strip() if await date_el.count() > 0 else ""
    except Exception:
        paper["date"] = ""

    try:
        # CNKI 可能通过 JS 动态加载引用次数，先尝试 td.quote 内的链接
        cite_el = row.locator("td.quote a").first
        if await cite_el.count() > 0:
            paper["cited_count"] = ((await cite_el.text_content()) or "0").strip()
        else:
            # 回退：直接取 td.quote 文本
            cite_td = row.locator("td.quote").first
            text = ((await cite_td.text_content()) or "").strip() if await cite_td.count() > 0 else ""
            paper["cited_count"] = text if text else "0"
    except Exception:
        paper["cited_count"] = "0"

    try:
        # CNKI 新页面结构：下载次数在 td.download > div > a.downloadCnt 中
        dl_el = row.locator("td.download a.downloadCnt").first
        if await dl_el.count() > 0:
            paper["download_count"] = ((await dl_el.text_content()) or "0").strip()
        else:
            dl_el = row.locator("td.download a").first
            paper["download_count"] = ((await dl_el.text_content()) or "0").strip() if await dl_el.count() > 0 else "0"
    except Exception:
        paper["download_count"] = "0"

    return paper


async def search_cnki_impl(
    page: Page,
    query: str,
    search_type: str = "主题",
    pages: int = 1,
    sort: str = "相关度",
) -> dict[str, Any]:
    """执行 CNKI 搜索并返回结果列表"""
    resolved_type = resolve_search_type(search_type)
    resolved_sort = resolve_sort_type(sort)
    all_papers: list[dict[str, Any]] = []

    await page.goto("https://www.cnki.net/", wait_until="domcontentloaded")
    await asyncio.sleep(random.uniform(1, 2))
    await dismiss_popups(page)
    await _check_cnki_accessible(page)

    if resolved_type != "主题":
        await select_search_type(page, resolved_type)

    search_box = page.locator(SELECTOR_SEARCH_INPUT)
    await search_box.wait_for(timeout=15_000)
    await search_box.fill(query)
    await asyncio.sleep(random.uniform(0.3, 0.6))
    await submit_search(page)
    await asyncio.sleep(random.uniform(3, 5))

    # 检测是否触发验证码
    current_url = page.url
    if "verify" in current_url:
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
            import logging
            logging.getLogger("cnki").warning(f"搜索页面 {page_num} 解析异常: {e}")

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

    return {
        "query": query,
        "search_type": resolved_type,
        "sort": resolved_sort,
        "total_pages": pages,
        "total_papers": len(all_papers),
        "papers": all_papers,
    }
