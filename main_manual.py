#This is the point where we will take all actions execution basically connects everything
from broker.zerodha_client import login_kite
from strategies.indicators import compute_indicators
from database.load_data import load_latest_data
from utils.utility import save_debug_csv
from utils.nse_scrapper import fetch_option_chain
from utils.db_func import store_market_data
from utils.db_func import fetch_market_data
from utils.data_fetcher import fetch_and_save_data


def main():
    DEBUG_SAVE = True
    """
    This is for testing on old data(csv) 
    #Loading it up(old data)
    df_1m = load_latest_data("NSEI", "1m")
    df_5m = load_latest_data("NSEI", "5m")
    df_15m = load_latest_data("NSEI", "15m")
    #print(df_1m)
    """
    spot_price, option_data = fetch_option_chain()

    if not option_data:
        print("[FATAL] No option data fetched.")
        exit()

    print(f"[INFO] Spot Price: {spot_price}")
    print(f"[INFO] Sample Option Row: {option_data[0]}")
    
 ##############-----LIVE MARKET API------##############################   

    """
    kite = login_kite()
    if kite: 
        print("Logged in successfully!!")

    secrets = load_config()
    print("Loaded config: ")
    print(secrets)
    """

############----For live market----######################
    """
    This is for live market values(api)

    """
    _, df_1m = fetch_and_save_data(symbol="^NSEI", period="5d", intervals=["1m"], return_interval="1m")
    _, df_5m = fetch_and_save_data(symbol="^NSEI", period="5d", intervals=["5m"], return_interval="5m")
    _, df_15m = fetch_and_save_data(symbol="^NSEI", period="5d", intervals=["15m"], return_interval="15m")
    df_1m = compute_indicators(df_1m)
    df_5m = compute_indicators(df_5m)
    df_15m = compute_indicators(df_15m)
    store_market_data(df_1m, symbol="^NSEI")
    store_market_data(df_5m, symbol="^NSEI")
    store_market_data(df_15m, symbol="^NSEI")

    
    #save the values for later on....
    if DEBUG_SAVE:
        #Saving values for debugging later on....
        save_debug_csv(df_1m, "NSEI_1m_with_stuff")
        save_debug_csv(df_5m, "NSEI_5m_with_stuff")
        save_debug_csv(df_15m, "NSEI_15m_with_stuff")

if __name__ == "__main__":
    main()