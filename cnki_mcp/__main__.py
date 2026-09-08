"""cnki_mcp 命令行入口。

用法:
    python -m cnki_mcp                # MCP stdio 模式（默认，供 Agent 客户端）
    python -m cnki_mcp trust          # 有头模式打开浏览器，手动过滑块后保存信任态
    python -m cnki_mcp --headed       # MCP 模式但浏览器有头（调试）
"""

import asyncio
import os
import sys


async def _trust_main() -> int:
    """有头模式打开 CNKI 首页，等用户手动完成验证后保存信任状态"""
    from cnki_mcp.browser import AsyncBrowserPool

    print("=" * 60)
    print("CNKI 信任态建立（手动过滑块）")
    print("=" * 60)
    print("将打开浏览器窗口访问 CNKI。请在浏览器里：")
    print("  1. 若出现滑块/安全验证，手动拖拽完成")
    print("  2. 可选：搜一次关键词，确认能出结果")
    print("  3. 回到此终端按回车，保存信任状态")
    print("     （状态保存后，MCP 重启不再触发滑块）")
    print()

    pool = AsyncBrowserPool()
    page = await pool.new_page()
    await pool.navigate_to_cnki(page)

    try:
        input("完成验证后按回车保存信任状态（Ctrl+C 取消）...")
    except (KeyboardInterrupt, EOFError):
        print("\n已取消")
        await pool.close()
        return 1

    path = await pool.save_trust_state()
    print(f"\n✅ 信任状态已保存: {path}")
    print("重启 MCP server 后生效。")
    await pool.close()
    return 0


def main() -> None:
    """CLI 入口"""
    args = sys.argv[1:]
    if args and args[0] == "trust":
        sys.exit(asyncio.run(_trust_main()))

    # MCP 模式：支持 --headed 透传给浏览器池（调试用）
    if "--headed" in args:
        os.environ["CNKI_HEADLESS"] = "0"
    from cnki_mcp.server import main as mcp_main

    mcp_main()


if __name__ == "__main__":
    main()
