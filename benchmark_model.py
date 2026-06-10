"""Quick benchmark: MiniMax-M2.7 vs MiniMax-M3.

Usage:
    python benchmark_model.py

Requires MINIMAX_API_KEY in .env or environment.
"""
import asyncio
import os
import time

import aiohttp
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("MINIMAX_API_KEY", "")
URL     = "https://api.minimax.io/anthropic/v1/messages"
MODELS  = ["MiniMax-M2.7", "MiniMax-M3"]
RUNS    = 3  # runs per model

SYSTEM = (
    "你係一個新聞分析助手。"
    "輸出一個 JSON 陣列，每篇新聞對應陣列內一個 object，按輸入編號順序排列，"
    "唔好有任何其他文字、解釋、markdown 或思考過程。"
    "陣列長度必須等於輸入新聞數量。\n"
    '每個 object 格式：\n'
    '{"summary":"單一字串（非array），5至8個重點，每點用「・」開頭，每點之間用換行符\\n分隔，每點唔超過10個字",'
    '"score":整數1到10（10=突發重大，5=一般新聞，1=普通資訊）,'
    '"tags":["標籤1","標籤2"]（最多3個中文標籤，唔帶#）,'
    '"sentiment":"positive"或"negative"或"neutral",'
    '"topic":"標準化話題名稱，唔超過10字",'
    '"event_type":"事件類型，2至6字，例如事故/政治/財經/天氣/娛樂/科技/法庭",'
    '"entities":{"people":["最多2個人物"],"companies":["最多2個公司/機構"],"places":["最多2個地點"],"dates":["最多2個日期"],"numbers":["最多2個關鍵數字"]},'
    '"key_sentences":["原文逐字摘錄最關鍵嘅 3 至 5 句句子（必須完全一致，唔好改寫，每句 10-80 字）"],'
    '"upcoming_events":[{"date":"YYYY-MM-DD","title":"短描述，唔超過20字"}]}'
)

SAMPLE_ARTICLE = """[1] 標題：港鐵荃灣線列車故障 多站延誤逾30分鐘
內容：港鐵今日下午3時許，荃灣線美孚站至荃灣站段發生列車故障，導致服務延誤超過30分鐘。
受影響乘客需在月台等候，港鐵已派員疏導人群，並提供免費接駁巴士服務。
港鐵發言人表示，技術人員正積極搶修，估計需時約1小時恢復正常服務。
事件影響約5000名乘客，港鐵對乘客帶來的不便深表歉意。
運輸署表示已密切監察情況，並已要求港鐵盡快恢復服務。"""


async def call_model(session: aiohttp.ClientSession, model: str) -> tuple[float, int, str]:
    start = time.perf_counter()
    async with session.post(
        URL,
        headers={
            "x-api-key":         API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        },
        json={
            "model":      model,
            "max_tokens": 1024,
            "system":     SYSTEM,
            "messages":   [{"role": "user", "content": SAMPLE_ARTICLE}],
        },
        timeout=aiohttp.ClientTimeout(total=60),
    ) as resp:
        data = await resp.json(content_type=None)

    elapsed = time.perf_counter() - start
    blocks  = data.get("content") or []
    text    = next((b.get("text", "") for b in blocks if b.get("type") == "text"), "")
    tokens  = data.get("usage", {}).get("output_tokens", 0)
    return elapsed, tokens, text[:80]


async def benchmark_model(model: str):
    print(f"\n{'-'*50}")
    print(f"  Model: {model}  ({RUNS} runs)")
    print(f"{'-'*50}")
    times, tokens_list = [], []
    async with aiohttp.ClientSession() as session:
        for i in range(RUNS):
            t, tok, preview = await call_model(session, model)
            times.append(t)
            tokens_list.append(tok)
            status = "OK" if tok > 0 else "FAIL"
            print(f"  Run {i+1}: {t:.2f}s  {tok} output tokens  {status}")
            if i == 0:
                print(f"  Preview: {preview}…")

    if times:
        avg_t = sum(times) / len(times)
        avg_tok = sum(tokens_list) / len(tokens_list)
        tps = avg_tok / avg_t if avg_t > 0 else 0
        print(f"\n  Avg latency : {avg_t:.2f}s")
        print(f"  Avg tokens  : {avg_tok:.0f}")
        print(f"  Tokens/sec  : {tps:.1f}")
    return times, tokens_list


async def main():
    if not API_KEY:
        print("ERROR: MINIMAX_API_KEY not set")
        return

    print("MiniMax M2.7 vs M3 — latency benchmark")
    print(f"Prompt: 1 article, same system prompt as rss-news")

    results = {}
    for model in MODELS:
        times, tokens = await benchmark_model(model)
        results[model] = {"times": times, "tokens": tokens}

    print(f"\n{'='*50}")
    print("  SUMMARY")
    print(f"{'='*50}")
    for model, r in results.items():
        if r["times"]:
            avg_t   = sum(r["times"]) / len(r["times"])
            avg_tok = sum(r["tokens"]) / len(r["tokens"])
            tps     = avg_tok / avg_t if avg_t > 0 else 0
            print(f"  {model:<18} {avg_t:.2f}s avg  {tps:.1f} tok/s")

    m27 = results.get("MiniMax-M2.7", {}).get("times", [])
    m3  = results.get("MiniMax-M3",   {}).get("times", [])
    if m27 and m3:
        diff = (sum(m27)/len(m27)) - (sum(m3)/len(m3))
        if diff > 0:
            print(f"\n  M3 faster by {diff:.2f}s on average")
        else:
            print(f"\n  M2.7 faster by {-diff:.2f}s on average")


if __name__ == "__main__":
    asyncio.run(main())
