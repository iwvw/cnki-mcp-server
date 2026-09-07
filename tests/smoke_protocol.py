"""协议级 stdio 冒烟测试：验证 MCP server 真实可用（工具注册 + 离线工具调用）"""

import asyncio
import json


async def main():
    proc = await asyncio.create_subprocess_exec(
        "python", "-m", "cnki_mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def send(msg):
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()
        request_id = msg.get("id")
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), 30)
            resp = json.loads(line)
            # 跳过通知（如 notifications/message 日志），只等带 id 的响应
            if "id" in resp and (request_id is None or resp["id"] == request_id):
                return resp

    init = await send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "smoke", "version": "1.0"}}})
    print("server:", init["result"]["serverInfo"])

    # 通知无响应，只写入不等待回复
    proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode())
    await proc.stdin.drain()

    lst = await send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    print("tools:", sorted(t["name"] for t in lst["result"]["tools"]))

    call = await send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "format_citation",
        "arguments": {
            "title": "深度学习在医学图像分析中的应用",
            "authors": "张三,李四,王五",
            "source": "中国医学杂志",
            "year": "2025", "volume": "45", "issue": "3", "pages": "12-20",
        }}})
    payload = json.loads(call["result"]["content"][0]["text"])
    print("citation:", payload["citation"])

    # 验证新参数 schema 真的暴露给客户端（year_from / journals）
    schema = next(t for t in lst["result"]["tools"] if t["name"] == "search_cnki")
    props = schema["inputSchema"]["properties"]
    print("search_cnki new params:", [k for k in props if k in ("year_from", "year_to", "journals")])

    proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), 10)
    except TimeoutError:
        proc.kill()
    print("SMOKE OK")


asyncio.run(main())