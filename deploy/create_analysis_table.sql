-- Optional helper script for local SQLite bootstrap.
-- Usage:
--   sqlite3 ../data.db < create_analysis_table.sql

CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    follower_count INTEGER,
    posts_analyzed INTEGER,
    reels_performance INTEGER,
    reels_90d_count INTEGER,
    reels_above_150pct INTEGER,
    low_performing_reels INTEGER,
    bottom10_avg_views REAL,
    post_engagement INTEGER,
    engagement_rate_pct REAL,
    total_interactions INTEGER,
    monetization INTEGER,
    monetization_signals TEXT,
    monetization_reason TEXT,
    youtube_url TEXT,
    twitter_url TEXT,
    twitter_followers INTEGER,
    other_socials TEXT,
    error TEXT,
    llm_cost_usd REAL DEFAULT 0,
    xpoz_results_used INTEGER DEFAULT 0,
    icp TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    _rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT,
    email TEXT,
    fol_cnt TEXT
);

CREATE INDEX IF NOT EXISTS idx_ar_username ON analysis_results(username);
CREATE INDEX IF NOT EXISTS idx_ar_analyzed_at ON analysis_results(analyzed_at DESC);
CREATE INDEX IF NOT EXISTS idx_ar_reels_perf ON analysis_results(reels_performance);
CREATE INDEX IF NOT EXISTS idx_ar_low_reels ON analysis_results(low_performing_reels);
CREATE INDEX IF NOT EXISTS idx_ar_engagement ON analysis_results(post_engagement);
CREATE INDEX IF NOT EXISTS idx_ar_monetization ON analysis_results(monetization);
CREATE INDEX IF NOT EXISTS idx_ar_follower_count ON analysis_results(follower_count);
CREATE INDEX IF NOT EXISTS idx_ar_eng_rate ON analysis_results(engagement_rate_pct);
CREATE INDEX IF NOT EXISTS idx_accounts_login ON accounts(login);
CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email);
CREATE INDEX IF NOT EXISTS idx_accounts_fol_cnt ON accounts(fol_cnt);
