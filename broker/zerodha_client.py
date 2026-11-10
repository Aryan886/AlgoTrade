#This will handle the data from zerodha api 
from kiteconnect import KiteConnect, KiteTicker
from utils.utility import load_config
import os
import weakref
from datetime import date

def login_kite():
    secrets = load_config()
    kite = KiteConnect(api_key=secrets['api_key'])

    print("Visit this URL and login:  ")
    print(kite.login_url())

    request_token = input("Paste the request token from URL here: ").strip()

    try: 
        data = kite.generate_session(request_token, api_secret=secrets['api_secret'])
        if isinstance(data, dict) and 'access_token' in data:
            access_token = data['access_token']
            profile = kite.profile()
            print("Logged in successfully!")
            print(profile)
        else:
            print("Unexpected response format from KiteConnect")
            return None

        #saving in file
        with open("access_token.txt", "w") as f:
            f.write(access_token)
        
        print("Access token was saved successfully")
        return kite
    
    except Exception as e:
        print("Error during login: ", e)
        return None
    
ACCESS_TOKEN_FILE = "access_token.txt"

# Instrument lists per client: {kite: {exchange: (day, instruments)}}.
# Keyed weakly so a replaced client (new login) drops its lists.
_instrument_cache = weakref.WeakKeyDictionary()


def get_instruments(kite, exchange, today=None):
    """
    Return the instrument list for an exchange, downloading it at most once
    per day per client. Kite publishes the list once a day, so re-downloading
    the multi-megabyte dump every minute is pure overhead.
    """
    today = today or date.today()
    try:
        per_client = _instrument_cache.setdefault(kite, {})
    except TypeError:
        # Client object cannot be weakly referenced; skip caching.
        return kite.instruments(exchange)

    cached = per_client.get(exchange)
    if cached is not None and cached[0] == today:
        return list(cached[1])

    instruments = kite.instruments(exchange)
    per_client[exchange] = (today, instruments)
    return list(instruments)

# One client per access token, shared by every caller in the process.
_cached_kite = None
_cached_token = None


def reset_kite_cache():
    """Forget the shared client (used by tests and after a manual re-login)."""
    global _cached_kite, _cached_token
    _cached_kite = None
    _cached_token = None


def kite_from_saved_token():
    """
    Return the shared Kite client for the saved access token.

    The client is built once and reused; a new one is built only when the
    token file changes (i.e. after a fresh login).
    """
    global _cached_kite, _cached_token

    try:
        with open(ACCESS_TOKEN_FILE, "r") as f:
            access_token = f.read().strip()

        if _cached_kite is not None and access_token == _cached_token:
            return _cached_kite

        secrets = load_config()
        kite = KiteConnect(api_key=secrets['api_key'])
        kite.set_access_token(access_token)
        _cached_kite = kite
        _cached_token = access_token
        return kite
    except FileNotFoundError:
        print("Access token file not found. Please run login_kite() first to generate a new token.")
        return None
    except Exception as e:
        print(f"Error loading access token: {e}")
        return None

if __name__=="__main__":
    login_kite()