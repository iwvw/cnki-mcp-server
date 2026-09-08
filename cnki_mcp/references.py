"""
CNKI 论文「参考文献列表」与「引证文献列表」抓取。

- 参考文献 (references): 这篇论文引用了哪些文献（文献综述滚雪球向前找）
- 引证文献 (citations):  哪些论文引用了这篇（向前追踪后续研究）

数据源：详情页的参考文献/引证文献 tab，内容经异步接口加载。
采用「接口直调 + DOM 解析 + 全文兜底」三层策略。
"""

import asyncio
import random
import re
from typing import Any

from playwright.async_api import Page


# 参考文献/引证文献异步接口（kns8s 详情页 tab 数据源，带 referer 校验）
_REF_LIST_URL = "/kns8s/Detail/GetRefList"
_CITE_LIST_URL = "/kns8s/Detail/GetCiteList"


def _parse_ref_items(html: str) -> list[dict[str, str]]:
    """从参考文献列表 HTML 中解析条目 [序号, 题名, 作者, 来源, 年份]"""
    items: list[dict[str, str]] = []
    # 常见结构：<li><span class="ref-no">[1]</span>题名... 作者. 来源, 年份.</li>
    lis = re.findall(r"<li[^>]*>(.*?)</li>", html, re.S)
    if not lis:
        # 回退：按 <p> / <div class="ref-item">
        lis = re.findall(
            r'<(?:p|div)[^>]*class=["\']?(?:ref-item|ref-text)[^"\']*["\']?>(.*?)</(?:p|div)>',
            html, re.S,
        )
    for li in lis:
        text = re.sub(r"<!--.*?-->", "", li, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        item: dict[str, str] = {"序号": "", "题名": "", "作者": "", "来源": "", "年份": ""}
        # 序号 [n]
        m = re.match(r"\[(\d+)\]", text)
        if m:
            item["序号"] = m.group(1)
            text = text[m.end():].strip()
        # 作者：到第一个「句点(中/英)+空白或结尾」为止
        m = re.match(r"^(.*?)[.\u3002](?:\s+|$)", text)
        if m and m.group(1).strip():
            item["作者"] = m.group(1).strip()
            text = text[m.end():].strip()
        # 题名：到 [J]/[D]/[C]/[M] 等文献类型标签为止
        m = re.search(r"^(.+?)(?:\[[A-Z]{1,6}(?:/[A-Z]{1,6})?\])", text)
        if m:
            item["题名"] = m.group(1).strip().strip("《》“”\"'")
            text = text[m.end():].strip()
        # 来源 + 年份：年份前逗号段为来源
        ym = re.search(r"(19|20)\d{2}", text)
        if ym:
            item["年份"] = ym.group(0)
            head = text[: ym.start()].strip(" ,，:")
            m2 = re.match(r"^(.+?)[,，]", head) or re.match(r"^([^\s]+)", head)
            if m2:
                item["来源"] = m2.group(1).strip()
            elif head:
                item["来源"] = head
            else:
                item["来源"] = "（未知来源）"
        items.append(item)
    return items


async def _load_tab_json(page: Page, url: str, file_id: str) -> list[dict[str, str]]:
    """调用详情页 tab 的异步接口（page.evaluate fetch：同源带 cookie）"""
    js = """async ({ url, fileId }) => {
        const fd = new URLSearchParams();
        fd.append('filename', fileId);
        const r = await fetch(url, { method: 'POST', body: fd,
            headers: {'X-Requested-With': 'XMLHttpRequest'} });
        return await r.text();
    }"""
    html = await page.evaluate(js, {"url": url, "fileId": file_id})
    return _parse_ref_items(html)


def _extract_file_id(url: str) -> str:
    """从详情页 URL 提取 filename（v= 参数）作为接口入参"""
    m = re.search(r"[?&]v=([^&]+)", url)
    if m:
        return m.group(1)
    return ""


async def get_references_impl(page: Page, url: str) -> dict[str, Any]:
    """获取论文的参考文献列表（这篇引用了哪些）"""
    return await _get_refs_core(page, url, is_citations=False)


async def get_citations_impl(page: Page, url: str) -> dict[str, Any]:
    """获取论文的引证文献列表（哪些引用了这篇）"""
    return await _get_refs_core(page, url, is_citations=True)


async def _get_refs_core(page: Page, url: str, is_citations: bool) -> dict[str, Any]:
    """参考文献/引证文献抓取核心"""
    kind = "引证文献" if is_citations else "参考文献"
    result: dict[str, Any] = {
        "url": url,
        "kind": kind,
        "total": 0,
        "items": [],
        "source": "",
        "error": "",
    }

    # 先访问详情页建立会话（与 get_paper_detail 同链路）
    try:
        await page.goto("https://www.cnki.net/", wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(random.uniform(1, 2))
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(random.uniform(2, 3))
    except Exception as e:
        result["error"] = f"详情页导航失败: {e}"
        return result

    # 检测验证码/空页
    content = await page.content()
    if len(content) < 200 or "verifybox" in content or "安全验证" in content:
        result["error"] = "CNKI 详情页触发了安全验证（滑块），请稍后重试"
        return result

    file_id = _extract_file_id(url)
    if file_id:
        api = _REF_LIST_URL if not is_citations else _CITE_LIST_URL
        items = await _load_tab_json(page, api, file_id)
        if items:
            result["items"] = items
            result["total"] = len(items)
            result["source"] = "api"
            return result

    # 回退：点击 tab 解析 DOM
    tab_text = "引证文献" if is_citations else "参考文献"
    try:
        tab = page.locator(f'a:has-text("{tab_text}"), .tab-item:has-text("{tab_text}"), [onclick*="{tab_text}"]').first
        if await tab.count() > 0 and await tab.is_visible():
            await tab.click()
            await asyncio.sleep(random.uniform(1.5, 2.5))
            content = await page.content()
    except Exception:
        pass

    items = _parse_ref_items(content)
    if items:
        result["items"] = items
        result["total"] = len(items)
        result["source"] = "dom"
        return result

    result["error"] = "未能解析到" + kind + "（接口与页面均未返回数据）"
    return result