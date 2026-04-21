from src.fetch import _parse_title_translations


def test_parse_title_translations_accepts_json_array():
    raw = '["蘋果宣布新產品", "AI 晶片需求上升"]'

    assert _parse_title_translations(raw, 2) == ["蘋果宣佈新產品", "AI 晶片需求上升"]


def test_parse_title_translations_rejects_wrong_length():
    raw = '["只有一個標題"]'

    assert _parse_title_translations(raw, 2) is None
