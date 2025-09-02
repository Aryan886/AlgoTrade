from broker.zerodha_client import kite_from_saved_token
from datetime import datetime

def test_option_ltp(symbol="NIFTY", max_contracts=10):
    kite = kite_from_saved_token()
    if not kite:
        print("Failed to connect to Kite API")
        return

    instruments = kite.instruments("NFO")
    current_date = datetime.now().date()

    option_insts = []
    for inst in instruments:
        name = str(inst.get("name", "")).upper()
        itype = str(inst.get("instrument_type", "")).upper()

        # Match index options
        if "NIFTY" in name and itype in ["CE", "PE", "OPTCE", "OPTPE"]:
            exp = inst.get("expiry")
            # expiry might already be a datetime.date
            if isinstance(exp, datetime):
                exp_date = exp.date()
            elif hasattr(exp, "date"):
                exp_date = exp.date()
            else:
                exp_date = exp
            if exp_date and exp_date >= current_date:
                option_insts.append(inst)

    if not option_insts:
        print("No NIFTY option instruments found. Check instrument filters.")
        return

    # Limit contracts for testing
    option_insts = option_insts[:max_contracts]

    print(f"\n{'Symbol':<20} {'Strike':>8} {'Type':>6} {'Raw LTP':>10} {'BestBid':>10} {'BestAsk':>10} {'Synthetic LTP':>15}")
    print("-" * 90)

    for inst in option_insts:
        ts = f"NFO:{inst['tradingsymbol']}"
        try:
            q = kite.quote(ts)
            q = q.get(ts, {})

            raw_ltp = q.get("last_price") or q.get("lastPrice") or q.get("ltp") or 0.0
            depth = q.get("depth") or {}
            buys = depth.get("buy") or []
            sells = depth.get("sell") or []

            bestBid = buys[0].get("price") if buys else 0.0
            bestAsk = sells[0].get("price") if sells else 0.0

            # Synthetic LTP calculation
            if bestBid and bestAsk:
                synth_ltp = (float(bestBid) + float(bestAsk)) / 2.0
            elif bestBid:
                synth_ltp = float(bestBid)
            elif bestAsk:
                synth_ltp = float(bestAsk)
            else:
                synth_ltp = raw_ltp

            print(f"{inst['tradingsymbol']:<20} {inst['strike']:>8} {inst['instrument_type']:>6} "
                  f"{raw_ltp:>10.2f} {bestBid:>10.2f} {bestAsk:>10.2f} {synth_ltp:>15.2f}")

        except Exception as e:
            print(f"Error fetching {ts}: {e}")

if __name__ == "__main__":
    test_option_ltp(symbol="NIFTY", max_contracts=10)
