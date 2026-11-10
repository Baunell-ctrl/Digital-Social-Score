import pandas as pd

from src.models.lstm_binary import LABELS, add_binary_label, load_splits


def test_or_rule():
    df = pd.DataFrame([[0] * 6, [0, 0, 0, 0, 1, 0], [1, 1, 0, 0, 0, 0]], columns=LABELS)
    assert list(add_binary_label(df)["label"]) == [0, 1, 1]


def test_splits_deterministic_disjoint():
    train, dev, test = load_splits("cleaned")
    _, dev2, _ = load_splits("cleaned")
    assert list(dev["id"]) == list(dev2["id"])
    assert not set(train["id"]) & set(dev["id"])
    assert abs(train["label"].mean() - dev["label"].mean()) < 0.01
    assert len(test) == 6490
