"""技術指標 (technical indicators)，全部以 pandas Series 運算。"""
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """簡單移動平均。"""
    return series.rolling(window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    """指數移動平均。"""
    return series.ewm(span=window, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """相對強弱指標 RSI (0~100)。"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD，回傳 (macd_line, signal_line, histogram)。"""
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def kd(df: pd.DataFrame, window: int = 9, smooth: int = 3):
    """KD 隨機指標 (台股慣用 9,3,3)，回傳 (K, D)。

    df 需含 high / low / close 欄位。RSV = (收盤 - 近N日最低) / (近N日最高 - 近N日最低)。
    """
    low_min = df["low"].rolling(window).min()
    high_max = df["high"].rolling(window).max()
    rng = (high_max - low_min).replace(0, pd.NA)
    rsv = (df["close"] - low_min) / rng * 100
    k = rsv.ewm(alpha=1 / smooth, adjust=False).mean()
    d = k.ewm(alpha=1 / smooth, adjust=False).mean()
    return k, d


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """平均真實區間 ATR，用於設停損與評估波動。

    df 需含 high / low / close 欄位。
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(window).mean()


def rolling_high(series: pd.Series, window: int) -> pd.Series:
    """過去 window 期的最高價 (突破策略用)。"""
    return series.rolling(window).max()


def rolling_low(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).min()


def relative_strength(series: pd.Series, benchmark: pd.Series) -> pd.Series:
    """個股相對大盤的強度 (歐尼爾 CANSLIM 的 RS 概念)。"""
    return (series / series.iloc[0]) / (benchmark / benchmark.iloc[0])
