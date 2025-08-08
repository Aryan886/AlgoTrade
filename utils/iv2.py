"""
Enhanced IV calculator specifically optimized for VIX calculation accuracy.
"""
from scipy.stats import norm
from scipy.optimize import brentq
from datetime import datetime, time
import math
import numpy as np

class VIXOptimizedIVCalculator:
    def __init__(self, risk_free_rate=0.065, dividend_yield=0.01):
        """
        Initialize with market parameters optimized for VIX calculation.
        
        Args:
            risk_free_rate: Current risk-free rate (6.5% default)
            dividend_yield: Nifty dividend yield (1% default)
        """
        self.risk_free_rate = risk_free_rate
        self.dividend_yield = dividend_yield
        
        # Market hours for NSE
        self.market_open = time(9, 15)
        self.market_close = time(15, 30)
    
    def calculate_time_to_expiry_precise(self, expiry_date_str, current_time=None):
        """
        Calculate ultra-precise time to expiry for VIX calculation.
        Uses actual trading minutes remaining.
        
        Args:
            expiry_date_str: Expiry date in 'YYYY-MM-DD' format
            current_time: Current datetime (default: now)
            
        Returns:
            float: Time to expiry in years (using 365 days, not 365.25)
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
        
        # For VIX calculation, use exactly 365 days (not 365.25)
        # This matches CBOE methodology
        return time_diff.total_seconds() / (365 * 24 * 3600)
    
    def calculate_time_to_expiry_minutes(self, expiry_date_str, current_time=None):
        """
        Calculate time to expiry in minutes (useful for VIX interpolation).
        """
        if current_time is None:
            current_time = datetime.now()
        
        expiry = datetime.strptime(expiry_date_str, "%Y-%m-%d")
        expiry = expiry.replace(hour=15, minute=30, second=0, microsecond=0)
        
        time_diff = expiry - current_time
        
        if time_diff.total_seconds() <= 0:
            return 0.0
            
        return time_diff.total_seconds() / 60.0
    
    def black_scholes_price_high_precision(self, spot, strike, T, r, q, sigma, option_type):
        """
        Ultra-high precision Black-Scholes for VIX calculation.
        Uses higher precision arithmetic and more stable computation.
        """
        if T <= 1e-10 or sigma <= 1e-10:  # More precise epsilon
            if option_type.upper() == 'CE':
                return max(0, spot - strike)
            else:
                return max(0, strike - spot)
        
        # Use higher precision calculation
        sqrt_T = math.sqrt(T)
        
        # More numerically stable calculation of d1 and d2
        d1_numerator = math.log(spot / strike) + (r - q) * T
        d1_volatility_term = 0.5 * sigma * sigma * T
        d1 = (d1_numerator + d1_volatility_term) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        
        # Use high precision normal CDF
        if option_type.upper() == 'CE':
            return (spot * math.exp(-q * T) * norm.cdf(d1) - 
                   strike * math.exp(-r * T) * norm.cdf(d2))
        else:  # PE
            return (strike * math.exp(-r * T) * norm.cdf(-d2) - 
                   spot * math.exp(-q * T) * norm.cdf(-d1))
    
    def calculate_iv_for_vix(self, spot, strike, market_price, expiry_date_str, option_type, 
                            use_mid_price=True, bid_price=None, ask_price=None):
        """
        Calculate implied volatility specifically optimized for VIX calculation.
        
        Args:
            spot: Current spot price
            strike: Option strike price
            market_price: LTP or mid-price
            expiry_date_str: Expiry date in 'YYYY-MM-DD'
            option_type: 'CE' or 'PE'
            use_mid_price: If True and bid/ask provided, use mid-price instead of LTP
            bid_price: Bid price (optional)
            ask_price: Ask price (optional)
            
        Returns:
            dict: Enhanced result with VIX-specific metrics
        """
        # Use mid-price if available (more accurate for VIX)
        if use_mid_price and bid_price is not None and ask_price is not None:
            if bid_price > 0 and ask_price > 0:
                price_to_use = (bid_price + ask_price) / 2.0
                price_source = "mid_price"
            else:
                price_to_use = market_price
                price_source = "ltp"
        else:
            price_to_use = market_price
            price_source = "ltp"
        
        # Calculate time to expiry with higher precision
        T = self.calculate_time_to_expiry_precise(expiry_date_str)
        T_minutes = self.calculate_time_to_expiry_minutes(expiry_date_str)
        
        if T <= 0:
            return {
                'iv': None, 'delta': None, 'time_to_expiry': 0,
                'time_to_expiry_minutes': 0, 'price_source': price_source,
                'status': 'expired', 'error': 'Option expired'
            }
        
        # Enhanced input validation
        if price_to_use <= 0:
            return {
                'iv': None, 'delta': None, 'time_to_expiry': T,
                'time_to_expiry_minutes': T_minutes, 'price_source': price_source,
                'status': 'invalid', 'error': 'Invalid market price'
            }
        
        # More precise intrinsic value calculation
        if option_type.upper() == 'CE':
            intrinsic = max(0, spot - strike)
        else:
            intrinsic = max(0, strike - spot)
        
        # Allow small violations due to market microstructure (bid-ask spreads)
        if price_to_use < intrinsic - 0.05:  # 5 paisa tolerance
            return {
                'iv': None, 'delta': None, 'time_to_expiry': T,
                'time_to_expiry_minutes': T_minutes, 'price_source': price_source,
                'status': 'invalid', 'error': 'Price significantly below intrinsic value'
            }
        
        # Define objective function with higher precision
        def objective(sigma):
            return (self.black_scholes_price_high_precision(
                spot, strike, T, self.risk_free_rate, 
                self.dividend_yield, sigma, option_type) - price_to_use)
        
        try:
            # Use tighter bounds and higher precision for VIX calculation
            # VIX rarely exceeds 100%, so we can use tighter upper bound
            lower_bound = 0.005  # 0.5% min IV
            upper_bound = 1.5    # 150% max IV (conservative for Indian markets)
            
            # Check if solution exists in our bounds
            try:
                f_lower = objective(lower_bound)
                f_upper = objective(upper_bound)
                
                # If both have same sign, no solution in range
                if f_lower * f_upper > 0:
                    # Try wider bounds
                    upper_bound = 2.0
                    f_upper = objective(upper_bound)
                    
                    if f_lower * f_upper > 0:
                        return {
                            'iv': None, 'delta': None, 'time_to_expiry': T,
                            'time_to_expiry_minutes': T_minutes, 'price_source': price_source,
                            'status': 'no_solution', 
                            'error': f'No IV solution found. Price: {price_to_use}, Intrinsic: {intrinsic:.2f}'
                        }
                
            except Exception as e:
                return {
                    'iv': None, 'delta': None, 'time_to_expiry': T,
                    'time_to_expiry_minutes': T_minutes, 'price_source': price_source,
                    'status': 'bounds_error', 'error': f'Error checking bounds: {str(e)}'
                }
            
            # Find IV using high-precision bisection method
            iv = brentq(objective, lower_bound, upper_bound, 
                       maxiter=100, xtol=1e-8, rtol=1e-8)
            
            # Calculate delta and other Greeks
            delta = self.calculate_delta(spot, strike, T, iv, option_type)
            gamma = self.calculate_gamma(spot, strike, T, iv)
            theta = self.calculate_theta(spot, strike, T, iv, option_type)
            vega = self.calculate_vega(spot, strike, T, iv)
            
            return {
                'iv': iv,
                'delta': delta,
                'gamma': gamma,
                'theta': theta,
                'vega': vega,
                'time_to_expiry': T,
                'time_to_expiry_minutes': T_minutes,
                'price_source': price_source,
                'intrinsic_value': intrinsic,
                'time_value': price_to_use - intrinsic,
                'status': 'success',
                'error': None
            }
            
        except Exception as e:
            return {
                'iv': None, 'delta': None, 'time_to_expiry': T,
                'time_to_expiry_minutes': T_minutes, 'price_source': price_source,
                'status': 'calculation_failed', 'error': str(e)
            }
    
    def calculate_delta(self, spot, strike, T, iv, option_type):
        """Calculate option delta with higher precision."""
        if T <= 1e-10 or iv <= 1e-10:
            return 0.0
        
        d1 = ((math.log(spot / strike) + 
              (self.risk_free_rate - self.dividend_yield + 0.5 * iv * iv) * T) / 
              (iv * math.sqrt(T)))
        
        if option_type.upper() == 'CE':
            return math.exp(-self.dividend_yield * T) * norm.cdf(d1)
        else:  # PE
            return math.exp(-self.dividend_yield * T) * (norm.cdf(d1) - 1)
    
    def calculate_gamma(self, spot, strike, T, iv):
        """Calculate option gamma."""
        if T <= 1e-10 or iv <= 1e-10:
            return 0.0
        
        d1 = ((math.log(spot / strike) + 
              (self.risk_free_rate - self.dividend_yield + 0.5 * iv * iv) * T) / 
              (iv * math.sqrt(T)))
        
        return (math.exp(-self.dividend_yield * T) * norm.pdf(d1)) / (spot * iv * math.sqrt(T))
    
    def calculate_theta(self, spot, strike, T, iv, option_type):
        """Calculate option theta (per day)."""
        if T <= 1e-10 or iv <= 1e-10:
            return 0.0
        
        sqrt_T = math.sqrt(T)
        d1 = ((math.log(spot / strike) + 
              (self.risk_free_rate - self.dividend_yield + 0.5 * iv * iv) * T) / 
              (iv * sqrt_T))
        d2 = d1 - iv * sqrt_T
        
        if option_type.upper() == 'CE':
            theta = (-(spot * norm.pdf(d1) * iv * math.exp(-self.dividend_yield * T)) / (2 * sqrt_T) -
                    self.risk_free_rate * strike * math.exp(-self.risk_free_rate * T) * norm.cdf(d2) +
                    self.dividend_yield * spot * math.exp(-self.dividend_yield * T) * norm.cdf(d1))
        else:  # PE
            theta = (-(spot * norm.pdf(d1) * iv * math.exp(-self.dividend_yield * T)) / (2 * sqrt_T) +
                    self.risk_free_rate * strike * math.exp(-self.risk_free_rate * T) * norm.cdf(-d2) -
                    self.dividend_yield * spot * math.exp(-self.dividend_yield * T) * norm.cdf(-d1))
        
        return theta / 365  # Convert to per-day
    
    def calculate_vega(self, spot, strike, T, iv):
        """Calculate option vega (per 1% change in vol)."""
        if T <= 1e-10 or iv <= 1e-10:
            return 0.0
        
        d1 = ((math.log(spot / strike) + 
              (self.risk_free_rate - self.dividend_yield + 0.5 * iv * iv) * T) / 
              (iv * math.sqrt(T)))
        
        return spot * math.exp(-self.dividend_yield * T) * norm.pdf(d1) * math.sqrt(T) / 100
    
    def batch_calculate_iv_for_vix(self, options_data, use_mid_prices=True):
        """
        Batch calculate IV with VIX-specific optimizations.
        
        Args:
            options_data: List of dicts with keys:
                         ['spot', 'strike', 'ltp', 'bid', 'ask', 'expiry_date', 'option_type', 'oi']
            use_mid_prices: Use mid-prices instead of LTP when available
        
        Returns:
            List of enhanced IV calculation results
        """
        results = []
        for option in options_data:
            result = self.calculate_iv_for_vix(
                option['spot'], option['strike'], option['ltp'],
                option['expiry_date'], option['option_type'],
                use_mid_prices, 
                option.get('bid'), option.get('ask')
            )
            
            # Add original option data
            result['strike'] = option['strike']
            result['option_type'] = option['option_type']
            result['open_interest'] = option.get('oi', 0)
            result['ltp'] = option['ltp']
            result['bid'] = option.get('bid')
            result['ask'] = option.get('ask')
            
            results.append(result)
        
        return results

# Usage example optimized for VIX calculation
if __name__ == "__main__":
    # Initialize VIX-optimized calculator
    vix_iv_calc = VIXOptimizedIVCalculator()
    
    # Example with bid-ask spread
    result = vix_iv_calc.calculate_iv_for_vix(
        spot=24501.70,
        strike=24450,
        market_price=36.05,  # LTP
        #bid_price=22.5,   # Bid
        #ask_price=23.5,   # Ask
        expiry_date_str="2025-08-14",  # Next week expiry
        option_type="PE",
        use_mid_price=True  # Use mid-price for better accuracy
    )
    
    if result['status'] == 'success':
        print(f"IV: {result['iv']*100:.3f}%")
        print(f"Price source: {result['price_source']}")
        print(f"Time to expiry: {result['time_to_expiry']*365:.2f} days")
        print(f"Time value: {result['time_value']:.2f}")
        print(f"Vega: {result['vega']:.3f}")
    else:
        print(f"Error: {result['error']}")