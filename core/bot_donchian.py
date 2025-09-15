from base_trader import BaseTrader
from core.strat_donchian import donchian_ao_strategy

class PaperTraderDonchian(BaseTrader):
    def __init__(self, symbol="NIFTY50"):
        super().__init__(
            symbol=symbol,
            strategy_func=donchian_ao_strategy,
            position_file="active_position_donchian.json",
            name="donchian"
        )

if __name__ == "__main__":
    trader = PaperTraderDonchian("NIFTY50")
    print("Donchian PaperTrader initialized....")
    trader.main_trading_loop()
