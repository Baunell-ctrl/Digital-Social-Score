from src.preprocessing.cleaner import clean_text


def test_lowercase():
    assert clean_text("HELLO World") == "hello world"


def test_removes_emojis():
    assert clean_text("you are bad 😡🔥 ok") == "you are bad ok"


def test_removes_newlines():
    assert clean_text("line one\n\nline two\r\nline three") == "line one line two line three"


def test_removes_punctuation_keeps_contractions():
    assert clean_text("Don't do that!!! Really?, (yes).") == "don't do that really yes"


def test_anonymization_tags_lose_brackets():
    assert clean_text("[PERSON] is in [LOCATION]") == "person is in location"


def test_empty_and_non_string():
    assert clean_text("") == ""
    assert clean_text("!!! 😀 ...") == ""
    assert clean_text(None) == ""


def test_idempotent():
    once = clean_text("Wow!!! 😀 It's\nGREAT.")
    assert clean_text(once) == once
