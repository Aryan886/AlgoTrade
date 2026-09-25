CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE market_data_1m (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            ao_value REAL,
            donchian_upper REAL,
            donchian_lower REAL,
            donchian_mid REAL
        , SL, SH, vix_value REAL);
CREATE TABLE market_data_5m (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            ao_value REAL,
            donchian_upper REAL,
            donchian_lower REAL,
            donchian_mid REAL
        , SL, SH, vix_value REAL);
CREATE TABLE market_data_15m (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            ao_value REAL,
            donchian_upper REAL,
            donchian_lower REAL,
            donchian_mid REAL
        , SL, SH, vix_value REAL);
CREATE TABLE option_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strike_price INTEGER NOT NULL,
            option_type TEXT NOT NULL,  -- 'CE' or 'PE'
            ltp REAL,
            iv REAL,
            expiry_date TEXT NOT NULL,
            spot_price REAL,
            tradingsymbol TEXT NOT NULL,
            open_interest INTEGER
        );
CREATE TABLE delta_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strike_price INTEGER NOT NULL,
            option_type TEXT NOT NULL,  -- 'CE' or 'PE'
            delta REAL,
            expiry_date TEXT NOT NULL,
            spot_price REAL,
            symbol TEXT NOT NULL
        , ltp REAL, tradingsymbol TEXT);
CREATE TABLE market_sma_1m(
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            sma_5 REAL,
            sma_20 REAL, sma_200 REAL, sma_50 REAL, sma_5_high REAL, sma_5_low REAL,
            PRIMARY KEY (timestamp, symbol)
        );
CREATE TABLE market_sma_5m(
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            sma_5 REAL,
            sma_20 REAL, sma_50 REAL, sma_200 REAL, sma_5_high REAL, sma_5_low REAL,
            PRIMARY KEY (timestamp, symbol)
        );
CREATE TABLE market_sma_15m(
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            sma_5 REAL,
            sma_20 REAL, sma_50 REAL, sma_200 REAL, sma_5_high REAL, sma_5_low REAL,
            PRIMARY KEY (timestamp, symbol)
        );
CREATE TABLE equity_data_1m (
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            vwap REAL, ao_value REAL, ao_color INTEGER, donchian_upper REAL, donchian_lower REAL, donchian_mid REAL,
            PRIMARY KEY (timestamp, symbol)
        );
CREATE TABLE equity_data_5m (
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            vwap REAL, ao_value REAL, ao_color INTEGER, donchian_upper REAL, donchian_lower REAL, donchian_mid REAL,
            PRIMARY KEY (timestamp, symbol)
        );
CREATE TABLE equity_data_15m (
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            vwap REAL, ao_value REAL, ao_color INTEGER, donchian_upper REAL, donchian_lower REAL, donchian_mid REAL,
            PRIMARY KEY (timestamp, symbol)
        );
CREATE TABLE equity_sma_1m (
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            sma_5 REAL,
            sma_20 REAL,
            sma_5_high REAL,
            sma_5_low REAL,
            sma_20_high REAL,
            sma_20_low REAL,
            PRIMARY KEY (timestamp, symbol)
        );
CREATE TABLE equity_sma_5m (
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            sma_5 REAL,
            sma_20 REAL,
            sma_5_high REAL,
            sma_5_low REAL,
            sma_20_high REAL,
            sma_20_low REAL,
            PRIMARY KEY (timestamp, symbol)
        );
CREATE TABLE equity_sma_15m (
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            sma_5 REAL,
            sma_20 REAL,
            sma_5_high REAL,
            sma_5_low REAL,
            sma_20_high REAL,
            sma_20_low REAL,
            PRIMARY KEY (timestamp, symbol)
        );
CREATE TABLE vix_data (
            timestamp TEXT PRIMARY KEY,
            symbol TEXT,
            vix_value REAL,
            vix_ao_value REAL,
            vix_donchian_upper REAL,
            vix_donchian_lower REAL,
            vix_donchian_mid REAL
        );
CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal TEXT,
            reason TEXT,
            confidence_score REAL
        );
CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            action TEXT,
            price REAL,
            qty INTEGER,
            status TEXT
        );
CREATE TABLE options_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            option_type TEXT NOT NULL,  -- 'CE' or 'PE'
            strike_price REAL NOT NULL,
            expiry_date TEXT NOT NULL,
            tradingsymbol TEXT NOT NULL,
            last_price REAL,
            open_interest INTEGER,
            implied_volatility REAL,
            delta REAL,
            spot_price REAL,
            atm_strike REAL
        );
CREATE TABLE market_sma_1h(
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            sma_5 REAL,
            sma_20 REAL,
            sma_50 REAL,
            sma_200 REAL, sma_5_high REAL, sma_5_low REAL,
            PRIMARY KEY (timestamp, symbol)
        );
CREATE INDEX idx_market_data_1m_symbol_timestamp
        ON market_data_1m (symbol, timestamp)
    ;
CREATE INDEX idx_market_data_5m_symbol_timestamp
        ON market_data_5m (symbol, timestamp)
    ;
CREATE INDEX idx_market_data_15m_symbol_timestamp
        ON market_data_15m (symbol, timestamp)
    ;
CREATE UNIQUE INDEX ux_market_data_1m_symbol_timestamp
            ON market_data_1m (symbol, timestamp);
CREATE UNIQUE INDEX ux_market_data_5m_symbol_timestamp
            ON market_data_5m (symbol, timestamp);
CREATE UNIQUE INDEX ux_market_data_15m_symbol_timestamp
            ON market_data_15m (symbol, timestamp);
CREATE TABLE market_data_1h (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            ao_value REAL,
            donchian_upper REAL,
            donchian_lower REAL,
            donchian_mid REAL,
            vix_value REAL,
            SL REAL,
            SH REAL
        );
CREATE UNIQUE INDEX ux_market_data_1h_symbol_timestamp
        ON market_data_1h (symbol, timestamp);
CREATE TABLE option_open_interest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            tradingsymbol TEXT NOT NULL,
            instrument_token INTEGER,
            expiry_date TEXT NOT NULL,
            strike_price REAL NOT NULL,
            option_type TEXT NOT NULL,
            spot_price REAL,
            last_price REAL,
            open_interest INTEGER,
            oi_day_high INTEGER,
            oi_day_low INTEGER,
            vwap REAL,
            last_trade_time TEXT,
            volume INTEGER
        );
CREATE UNIQUE INDEX ux_option_oi_timestamp_symbol_tradingsymbol
        ON option_open_interest (timestamp, symbol, tradingsymbol);
CREATE INDEX idx_option_oi_symbol_timestamp
        ON option_open_interest (symbol, timestamp);
CREATE INDEX idx_option_oi_contract_replay
        ON option_open_interest (symbol, expiry_date, strike_price, option_type, timestamp);
CREATE TABLE option_open_interest_5m (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            tradingsymbol TEXT NOT NULL,
            instrument_token INTEGER,
            expiry_date TEXT NOT NULL,
            strike_price REAL NOT NULL,
            option_type TEXT NOT NULL,
            spot_price REAL,
            last_price REAL,
            open_interest INTEGER,
            oi_day_high INTEGER,
            oi_day_low INTEGER,
            vwap REAL,
            bucket_volume INTEGER,
            is_carry_forward INTEGER NOT NULL DEFAULT 0,
            source_start_timestamp TEXT,
            source_end_timestamp TEXT,
            source_snapshot_count INTEGER,
            last_trade_time TEXT
        );
CREATE UNIQUE INDEX ux_option_oi_5m_timestamp_symbol_tradingsymbol
        ON option_open_interest_5m (timestamp, symbol, tradingsymbol);
CREATE INDEX idx_option_oi_5m_symbol_timestamp
        ON option_open_interest_5m (symbol, timestamp);
CREATE INDEX idx_option_oi_5m_contract_replay
        ON option_open_interest_5m (symbol, expiry_date, strike_price, option_type, timestamp);
CREATE TABLE dashboard_strategy_state (
                strategy_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                last_started_at TEXT,
                last_stopped_at TEXT,
                updated_at TEXT NOT NULL
            );
CREATE TABLE dashboard_trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                entry_ts TEXT NOT NULL,
                exit_ts TEXT,
                side TEXT NOT NULL,
                qty INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                realized_pnl REAL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                position_type TEXT,
                section TEXT,
                subcat TEXT,
                exit_reason TEXT,
                sl_initial REAL,
                sl_final REAL,
                sl_adjustments INTEGER NOT NULL DEFAULT 0
            );
