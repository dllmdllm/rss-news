"""香港繁體正規化。

zhconv 個 `zh-hk`（同 `zh-hant`）表一律將「咸」轉做「鹹」——簡體嘅「咸」
一個字兼任「全部」（咸豐）同「鹹」（鹹魚）兩個意思，反向轉換冇上下文
就一律當「鹹」。對真．簡體輸入嚟講咁樣多數啱，但對**已經係繁體**嘅文字
就純粹係破壞：

    碧咸（Beckham）    → 碧鹹
    英格咸（Ingraham）  → 英格鹹
    咸豐               → 鹹豐

2026-07-30 加 HuffPost 娛樂之後西方藝人名會多，實測 MiniMax M3 譯
"David Beckham" 出正確嘅「碧咸」，跟住就俾 zhconv 整成「碧鹹」。

所以分兩個入口，按 caller 知唔知個輸入係咩語言嚟揀，唔靠估：

- `from_simplified()` — 真．簡體來源（`SIMPLIFIED_SOURCES`，即 cnBeta）。
  照用 zhconv 全套，「咸鱼」照樣變「鹹魚」。
- `to_hk()` — 已經係繁體、淨係想掃走零星簡體漏網（MiniMax 譯文——實測會出
  「湯告鲁斯」；AI 抽出嚟嘅實體名；Google Trends 熱字）。保護「咸」。

⚠️ 代價：如果一個真．簡體來源錯用咗 `to_hk()`，佢個「咸鱼」會停喺
「咸魚」。呢個係刻意取捨——簡體源寫鹹魚罕見，而香港譯名用「咸」愈嚟愈多。
"""
import zhconv

# Unicode private-use area：正常文本唔會出現，所以攞嚟做 placeholder 安全。
_PROTECTED = {"咸": ""}


def from_simplified(text: str) -> str:
    """真．簡體 → 香港繁體。冇保護，「咸」照樣變「鹹」。"""
    return zhconv.convert(str(text), "zh-hk")


def to_hk(text: str) -> str:
    """已經係繁體嘅文字掃走零星簡體漏網，唔郁香港譯名用嘅「咸」。"""
    s = str(text)
    # Placeholder 撞正真實文本（實際上唔會）就放棄保護，總好過搞爛個字串。
    if any(ph in s for ph in _PROTECTED.values()):
        return zhconv.convert(s, "zh-hk")
    for ch, ph in _PROTECTED.items():
        s = s.replace(ch, ph)
    s = zhconv.convert(s, "zh-hk")
    for ch, ph in _PROTECTED.items():
        s = s.replace(ph, ch)
    return s
