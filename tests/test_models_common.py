import numpy as np

from src.models.common import LABELS, compute_metrics, load_splits


def test_compute_metrics_perfect_and_known_case():
    y_true = np.array([[1, 0, 0, 0, 1, 0], [0, 0, 0, 0, 0, 0], [1, 1, 1, 0, 0, 1], [0, 0, 1, 1, 0, 0]])
    perfect = compute_metrics(y_true, y_true.astype(float))
    assert perfect["toxic"] == {"precision": 1.0, "recall": 1.0, "f1": 1.0, "roc_auc": 1.0}

    # toxic : prédit [1, 1, 0, 0] pour vrai [1, 0, 1, 0] -> précision 0,5, rappel 0,5
    y_prob = y_true.astype(float)
    y_prob[:, 0] = [0.9, 0.8, 0.2, 0.1]
    m = compute_metrics(y_true, y_prob)
    assert m["toxic"]["precision"] == 0.5 and m["toxic"]["recall"] == 0.5
    assert set(m["per_label"]) == set(LABELS)


def test_splits_are_deterministic_stratified_and_disjoint():
    train, dev, test = load_splits("cleaned")
    train2, dev2, _ = load_splits("cleaned")
    assert list(dev["id"]) == list(dev2["id"])
    assert not set(train["id"]) & set(dev["id"])
    assert len(train) + len(dev) == 25960 and len(dev) == 2596
    assert abs(train["toxic"].mean() - dev["toxic"].mean()) < 0.01
    assert len(test) == 6490
