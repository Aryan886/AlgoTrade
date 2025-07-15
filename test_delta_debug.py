from utils.black_scholes import calculate_black_scholes_delta
from datetime import date

# Example parameters for ATM option (strike = spot)
spot_price = 25062.45           # Current NIFTY spot price
strike_price = 25250.0          # ATM strike (rounded to nearest 50)
expiry_date = '2025-07-17'      # Next expiry (YYYY-MM-DD)
implied_volatility = 0.1254    # 12.54% IV (as decimal)
option_type = 'PE'              # 'CE' for call, 'PE' for put
risk_free_rate = 0.065          # 6.5% (default)

# Calculate time to expiry in years (using Black-Scholes helper)
from utils.black_scholes import calculate_time_to_expiry
T = calculate_time_to_expiry(expiry_date, date.today())

# Call the delta calculation (debug output will be printed)
delta = calculate_black_scholes_delta(
    spot_price=spot_price,
    strike_price=strike_price,
    time_to_expiry=T,
    implied_volatility=implied_volatility,
    option_type=option_type,
    risk_free_rate=risk_free_rate
)

print(f"\nFinal Delta (raw): {delta}")
print(f"Final Delta (NSE style): {delta * 100}") 