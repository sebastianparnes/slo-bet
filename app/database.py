import sqlite3
import os
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "data/betting_history.db")

def get_connection():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS bet_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT (datetime('now')),
            
            -- Match info
            match_id TEXT,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            league TEXT NOT NULL,
            match_date TEXT NOT NULL,
            
            -- Bet details
            bet_type TEXT NOT NULL,       -- '1X2', 'over_under', 'btts', 'double_chance'
            bet_selection TEXT NOT NULL,   -- '1', 'X', '2', 'over_2.5', 'yes', etc.
            
            -- Odds at time of bet
            odds REAL,
            
            -- Money
            stake REAL NOT NULL,
            potential_win REAL,
            actual_win REAL DEFAULT 0,
            
            -- Result
            result TEXT DEFAULT 'pending',  -- 'win', 'loss', 'void', 'pending'
            match_result TEXT,              -- final score like '2-1'
            
            -- Analysis score at time of bet
            confidence_score REAL,
            recommendation TEXT,
            
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS analysis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE,
            cached_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT,
            analysis_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_bet_history_date ON bet_history(match_date);
        CREATE INDEX IF NOT EXISTS idx_bet_history_result ON bet_history(result);
        CREATE INDEX IF NOT EXISTS idx_cache_expires ON analysis_cache(expires_at);
    """)
    
    conn.commit()
    conn.close()
    print("✅ Database initialized")

init_db()
