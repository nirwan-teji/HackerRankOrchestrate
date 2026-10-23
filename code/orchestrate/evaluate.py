"""Score predictions vs the sample/labelled file; compare strategies side by side."""
import pandas as pd


def accuracy(pred: pd.DataFrame, gold: pd.DataFrame, cols: list[str], key: str | None = None) -> dict:
    if key:
        pred, gold = pred.set_index(key), gold.set_index(key)
    pred, gold = pred.loc[gold.index], gold
    res = {c: float((pred[c].str.strip().str.lower() == gold[c].str.strip().str.lower()).mean()) for c in cols}
    res["mean"] = sum(res.values()) / len(cols)
    return res


def compare(results: dict[str, dict]) -> pd.DataFrame:
    """results = {'strategy_name': accuracy_dict}; paste the table into README (June required >=2 strategies)."""
    return pd.DataFrame(results).T.round(3)
