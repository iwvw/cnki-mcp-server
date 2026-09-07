"""
CNKI 论文标题快速匹配。

使用 difflib 序列匹配算法（比字符计数更接近人类判断），
适合验证论文标题或快速定位特定论文。
"""

import asyncio
import random
from difflib import SequenceMatcher
from typing import Any

from playwright.async_api import Page

from cnki_mcp.config import SELECTOR_RESULT_ROWS, SELECTOR_SEARCH_BTN, SELECTOR_SEARCH_INPUT
from cnki_mcp.search import _check_cnki_accessible, _goto_home_with_retry, parse_paper_row_async


def find_closest_title(query: str, titles: list[str]) -> int:
    """根据序列匹配度选择最接近的搜索结果，返回下标"""
    if not titles:
        return 0
    return max(
        range(len(titles)),
        key=lambda i: SequenceMatcher(None, query, titles[i]).ratio(),
    )


async def find_best_match_impl(page: Page, query: str) -> dict[str, Any]:
    """搜索并返回最匹配论文的标题和 URL"""
    await _goto_home_with_retry(page)

    search_box = page.locator(SELECTOR_SEARCH_INPUT)
    await search_box.wait_for(timeout=15_000)
    await search_box.fill(query)
    await asyncio.sleep(random.uniform(0.3, 0.6))

    # 使用原生点击提交搜索
    try:
        search_btn = page.locator(SELECTOR_SEARCH_BTN).first
        if await search_btn.count() > 0 and await search_btn.is_visible():
            await search_btn.click()
    except Exception:
        await page.locator(SELECTOR_SEARCH_INPUT).press("Enter")

    await asyncio.sleep(random.uniform(3, 5))

    # 检测验证码（URL 特征优先，再做内容级反爬检查）
    if "verify" in page.url:
        return {
            "query": query,
            "best_match": None,
            "isError": True,
            "error": "CNKI 触发了验证码，请稍后再试",
            "error_type": "CaptchaError",
        }
    await _check_cnki_accessible(page)

    result_titles: list[str] = []
    result_urls: list[str] = []

    try:
        await page.wait_for_selector(SELECTOR_RESULT_ROWS, timeout=15_000)
        rows = page.locator(SELECTOR_RESULT_ROWS)
        count = await rows.count()
        for i in range(count):
            try:
                paper = await parse_paper_row_async(rows.nth(i))
                if paper.get("title"):
                    result_titles.append(paper["title"])
                    result_urls.append(paper["url"])
            except Exception:
                pass
    except Exception:
        pass

    if not result_titles:
        return {"query": query, "best_match": None, "message": "未找到结果"}

    idx = find_closest_title(query, result_titles)
    return {
        "query": query,
        "best_match": {
            "title": result_titles[idx],
            "url": result_urls[idx],
        },
        "total_results": len(result_titles),
    }