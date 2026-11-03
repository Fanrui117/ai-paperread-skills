"""命名与文件后缀规则。"""
from paperread.naming import folder_name, guess_short, paper_files, sanitize, slugify


def test_guess_short_splits_on_colon():
    assert guess_short("TAP-Vid: A Benchmark for Tracking Any Point in a Video", "6XYJKYR5") == "TAP-Vid"
    assert guess_short("Orca: A Distributed Serving System", "45DU2RKX") == "Orca"


def test_guess_short_override():
    assert guess_short("Particle Video Revisited: Tracking...", "8VWR6W8W", {"8VWR6W8W": "PIPs"}) == "PIPs"


def test_folder_and_files():
    assert folder_name("TAP-Vid", "6XYJKYR5") == "TAP-Vid-6XYJKYR5"
    files = paper_files("TAP-Vid")
    assert files["note"] == "note.md"
    assert files["summary"] == "summary.md"
    assert files["fulltext"] == "full.md"
    assert files["src"] == "src.md"


def test_sanitize_windows_invalid_chars():
    assert sanitize('a<b>c:d"e/f\\g|h?i*j') == "a-b-c-d-e-f-g-h-i-j"
    assert slugify("Particle Video Revisited") == "Particle-Video-Revisited"
