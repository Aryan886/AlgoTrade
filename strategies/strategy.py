from indicators import sma
import pandas as pd


def multi_sma_strategy(df):
    df = df.copy()