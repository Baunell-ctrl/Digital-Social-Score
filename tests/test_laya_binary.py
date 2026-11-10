import pandas as pd

from src.models.laya_binary import LABELS, add_binary_label, load_splits


def test_or_rule():
    df = pd.DataFrame([[0] * 6, [0, 0, 0, 0, 1, 0], [1, 1, 0, 0, 0, 0], [0, 0, 0, 0, 0, 1]], columns=LABELS)
    assert list(add_binary_label(df)["label"]) == [0, 1, 1, 1]


def test_splits_match_lstm_and_bert_protocol():
    train, dev, test = load_splits("cleaned")
    assert (len(train), len(dev), len(test)) == (23364, 2596, 1308)
    assert not set(train["id"]) & set(dev["id"])
    assert not set(test["comment_text"]) & set(train["comment_text"])
    assert abs(train["label"].mean() - dev["label"].mean()) < 0.01
