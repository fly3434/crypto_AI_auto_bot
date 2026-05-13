import pandas as pd

from src.features import atr, rsi


def test_rsi_bounds():
    close = pd.Series([100, 101, 102, 101, 103, 104, 105, 103, 102, 106, 108, 107, 109, 110, 111, 112])
    values = rsi(close).dropna()
    assert ((values >= 0) & (values <= 100)).all()


def test_atr_positive():
    df = pd.DataFrame(
        {
            "high": [11, 12, 13, 14, 15],
            "low": [9, 10, 11, 12, 13],
            "close": [10, 11, 12, 13, 14],
        }
    )
    assert (atr(df).dropna() > 0).all()

