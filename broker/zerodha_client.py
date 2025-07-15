#This will handle the data from zerodha api 
from kiteconnect import KiteConnect, KiteTicker
from utils.utility import load_config
import os

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
    
def kite_from_saved_token():
    secrets = load_config()
    kite = KiteConnect(api_key=secrets['api_key'])

    try:
        with open("access_token.txt", "r") as f:
            access_token = f.read().strip()
        
        kite.set_access_token(access_token)
        return kite
    except FileNotFoundError:
        print("Access token file not found. Please run login_kite() first to generate a new token.")
        return None
    except Exception as e:
        print(f"Error loading access token: {e}")
        return None

if __name__=="__main__":
    login_kite()