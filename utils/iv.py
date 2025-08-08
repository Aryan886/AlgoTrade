"""
Production-ready IV calculator for live trading bot.
Optimized for speed and accuracy.
"""
from scipy.stats import norm
from scipy.optimize import brentq
from datetime import datetime, time
import math

class ProductionIVCalculator:
    def __init__(self, risk_free_rate=0.065, dividend_yield=0.01):
        """
        Initialize with market parameters.
        
        Args:
            risk_free_rate: Current risk-free rate (6.5% default)
            dividend_yield: Nifty dividend yield (1% default)
        """
        self.risk_free_rate = risk_free_rate
        self.dividend_yield = dividend_yield
        
        # Market hours for NSE
        self.market_open = time(9, 15)
        self.market_close = time(15, 30)
    
    def calculate_time_to_expiry(self, expiry_date_str, current_time=None):
        """
        Calculate precise time to expiry in years.
        
        Args:
            expiry_date_str: Expiry date in 'YYYY-MM-DD' format
            current_time: Current datetime (default: now)
            
        Returns:
            float: Time to expiry in years
        """
        if current_time is None:
            current_time = datetime.now()
        
        # Parse expiry date and set to 3:30 PM (NSE closing)
        expiry = datetime.strptime(expiry_date_str, "%Y-%m-%d")
        expiry = expiry.replace(hour=15, minute=30, second=0, microsecond=0)
        
        # Calculate time difference
        time_diff = expiry - current_time
        
        if time_diff.total_seconds() <= 0:
            return 0.0  # Expired
        
        # Convert to years
        return time_diff.total_seconds() / (365.25 * 24 * 3600)
    
    def black_scholes_price(self, spot, strike, T, r, q, sigma, option_type):
        """
        Calculate Black-Scholes price with dividend yield.
        Optimized for speed.
        """
        if T <= 0 or sigma <= 0:
            return 0.0
        
        sqrt_T = math.sqrt(T)
        d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        
        if option_type.upper() == 'CE':
            return (spot * math.exp(-q * T) * norm.cdf(d1) - 
                   strike * math.exp(-r * T) * norm.cdf(d2))
        else:  # PE
            return (strike * math.exp(-r * T) * norm.cdf(-d2) - 
                   spot * math.exp(-q * T) * norm.cdf(-d1))
    
    def calculate_iv(self, spot, strike, ltp, expiry_date_str, option_type):
        """
        Calculate implied volatility for live trading.
        
        Args:
            spot: Current spot price
            strike: Option strike price
            ltp: Current market price (LTP or mid-price)
            expiry_date_str: Expiry date in 'YYYY-MM-DD'
            option_type: 'CE' or 'PE'
            
        Returns:
            dict: {
                'iv': float or None,
                'delta': float or None,
                'time_to_expiry': float,
                'status': str,
                'error': str or None
            }
        """
        # Calculate time to expiry
        T = self.calculate_time_to_expiry(expiry_date_str)
        
        if T <= 0:
            return {
                'iv': None, 'delta': None, 'time_to_expiry': 0,
                'status': 'expired', 'error': 'Option expired'
            }
        
        # Input validation
        if ltp <= 0:
            return {
                'iv': None, 'delta': None, 'time_to_expiry': T,
                'status': 'invalid', 'error': 'Invalid market price'
            }
        
        # Check intrinsic value
        if option_type.upper() == 'CE':
            intrinsic = max(0, spot - strike)
        else:
            intrinsic = max(0, strike - spot)
        
        if ltp < intrinsic:
            return {
                'iv': None, 'delta': None, 'time_to_expiry': T,
                'status': 'invalid', 'error': 'Price below intrinsic value'
            }
        
        # Define objective function for root finding
        def objective(sigma):
            return (self.black_scholes_price(spot, strike, T, self.risk_free_rate, 
                                           self.dividend_yield, sigma, option_type) - 
                   ltp)
        
        try:
            # Find IV using bisection method
            iv = brentq(objective, 0.01, 2.0, maxiter=50, xtol=1e-6)
            
            # Calculate delta
            delta = self.calculate_delta(spot, strike, T, iv, option_type)
            
            return {
                'iv': iv,
                'delta': delta,
                'time_to_expiry': T,
                'status': 'success',
                'error': None
            }
            
        except Exception as e:
            return {
                'iv': None, 'delta': None, 'time_to_expiry': T,
                'status': 'failed', 'error': str(e)
            }
    
    def calculate_delta(self, spot, strike, T, iv, option_type):
        """Calculate option delta."""
        if T <= 0 or iv <= 0:
            return 0.0
        
        d1 = ((math.log(spot / strike) + 
              (self.risk_free_rate - self.dividend_yield + 0.5 * iv * iv) * T) / 
              (iv * math.sqrt(T)))
        
        if option_type.upper() == 'CE':
            return math.exp(-self.dividend_yield * T) * norm.cdf(d1)
        else:  # PE
            return math.exp(-self.dividend_yield * T) * (norm.cdf(d1) - 1)
    
    def is_market_open(self, current_time=None):
        """Check if market is currently open."""
        if current_time is None:
            current_time = datetime.now()
        
        current_time_only = current_time.time()
        return self.market_open <= current_time_only <= self.market_close
    
    def batch_calculate_iv(self, options_data):
        """
        Calculate IV for multiple options at once.
        
        Args:
            options_data: List of dicts with keys:
                         ['spot', 'strike', 'ltp', 'expiry_date', 'option_type']
        
        Returns:
            List of IV calculation results
        """
        results = []
        for option in options_data:
            result = self.calculate_iv(
                option['spot'], option['strike'], option['ltp'],
                option['expiry_date'], option['option_type']
            )
            result['strike'] = option['strike']
            result['option_type'] = option['option_type']
            results.append(result)
        
        return results

# Usage example for your trading bot
if __name__ == "__main__":
    # Initialize calculator
    iv_calc = ProductionIVCalculator()
    
    # Example calculation
    result = iv_calc.calculate_iv(
        spot=24501.70,
        strike=24400,
        ltp=101.5,  
        expiry_date_str="2025-08-14",
        option_type="PE"
    )
    
    if result['status'] == 'success':
        print(f"IV: {result['iv']*100:.2f}%")
        print(f"Delta(NSE): {result['delta']*100:.4f}")
        print(f"Time to expiry: {result['time_to_expiry']*365:.1f} days")
    else:
        print(f"Error: {result['error']}")
    
    """
    # Batch processing example
    options_batch = [
        {'spot': 25630, 'strike': 25600, 'ltp': 120, 'expiry_date': '2025-07-17', 'option_type': 'CE'},
        {'spot': 25630, 'strike': 25650, 'ltp': 85, 'expiry_date': '2025-07-17', 'option_type': 'CE'},
        {'spot': 25630, 'strike': 25700, 'ltp': 45, 'expiry_date': '2025-07-17', 'option_type': 'CE'},
    ]
    
    batch_results = iv_calc.batch_calculate_iv(options_batch)
    print("\nBatch Results:")
    for result in batch_results:
        if result['status'] == 'success':
            print(f"{result['strike']} {result['option_type']}: IV={result['iv']*100:.1f}%, Delta={result['delta']:.3f}")
    """