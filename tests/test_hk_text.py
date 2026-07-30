"""hk_text：zhconv 個「咸 → 鹹」問題同兩個入口嘅分工。"""
import re
from pathlib import Path

from src.hk_text import from_simplified, to_hk

ROOT = Path(__file__).resolve().parents[1]


# ---- to_hk：已經係繁體，保護「咸」 ---------------------------------------

def test_to_hk_keeps_hong_kong_names_that_use_咸():
    # 實測 MiniMax M3 譯 "David Beckham" 出「碧咸」，跟住俾 zhconv 整成「碧鹹」。
    assert to_hk("碧咸") == "碧咸"
    assert to_hk("羅拉英格咸") == "羅拉英格咸"
    assert to_hk("咸豐") == "咸豐"
    # 就算成句都係，唔可以有漏網
    assert "鹹" not in to_hk("碧咸與湯告魯斯出席溫布頓決賽")


def test_to_hk_still_sweeps_simplified_leftovers():
    # 保護「咸」唔可以順手令個 function 冇晒作用——MiniMax 實測會漏簡體出嚟
    # （「湯告鲁斯」），呢個先係 to_hk 存在嘅原因。
    assert to_hk("湯告鲁斯") == "湯告魯斯"
    assert to_hk("地区") == "地區"
    assert to_hk("碧咸喺该地区入球") == "碧咸喺該地區入球"


def test_to_hk_leaves_real_鹹_alone():
    assert to_hk("鹹魚") == "鹹魚"
    assert to_hk("鹹水管爆裂") == "鹹水管爆裂"


def test_to_hk_handles_empty_and_non_string():
    assert to_hk("") == ""
    assert to_hk(None) == "None"


# ---- from_simplified：真．簡體來源，唔保護 --------------------------------

def test_from_simplified_converts_咸_because_input_really_is_simplified():
    # cnBeta 寫「咸鱼」係想講鹹魚，唔係 Beckham。呢個入口冇保護係刻意嘅。
    assert from_simplified("咸鱼") == "鹹魚"
    assert from_simplified("地区") == "地區"


# ---- 每個 call site 都要揀啱入口 -----------------------------------------

def test_no_module_calls_zhconv_directly_any_more():
    """zhconv 收晒入 hk_text，其他 module 一律要經兩個入口之一——直接
    call zhconv.convert 就等於繞過咗「咸」保護，而且唔會有任何報錯。"""
    offenders = []
    for path in (ROOT / "src").glob("*.py"):
        if path.name == "hk_text.py":
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bzhconv\s*\.", line) or line.strip() == "import zhconv":
                offenders.append(f"{path.name}:{i}")
    assert not offenders, f"要用 hk_text 嘅入口，唔好直接 call zhconv：{offenders}"


def test_simplified_sources_use_the_unprotected_entry_point():
    # cnBeta 嘅標題同內文係真．簡體，一定要行 from_simplified。
    fetch = (ROOT / "src/fetch.py").read_text(encoding="utf-8")
    at = fetch.index("SIMPLIFIED_SOURCES:")
    assert "from_simplified(" in fetch[at:at + 200], "簡體來源標題要行 from_simplified"
    scrape = (ROOT / "src/scrape.py").read_text(encoding="utf-8")
    at = scrape.index("def _to_hk_traditional")
    assert "from_simplified(" in scrape[at:at + 200], "簡體來源內文要行 from_simplified"
