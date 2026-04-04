-- Xpoz Supabase schema
-- analysis_results is the current persisted enrichment/analysis table used by xpoz/deploy.

CREATE TABLE IF NOT EXISTS analysis_results (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    analyzed_at TIMESTAMPTZ DEFAULT NOW(),
    follower_count INTEGER,
    posts_analyzed INTEGER,
    reels_performance BOOLEAN,
    reels_90d_count INTEGER,
    reels_above_150pct INTEGER,
    low_performing_reels BOOLEAN,
    bottom10_avg_views DOUBLE PRECISION,
    post_engagement BOOLEAN,
    engagement_rate_pct DOUBLE PRECISION,
    total_interactions INTEGER,
    monetization BOOLEAN,
    monetization_signals TEXT[],
    monetization_reason TEXT,
    youtube_url TEXT,
    twitter_url TEXT,
    twitter_followers INTEGER,
    other_socials JSONB,
    error TEXT,
    llm_cost_usd DOUBLE PRECISION DEFAULT 0,
    xpoz_results_used INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ar_username       ON analysis_results (username);
CREATE INDEX IF NOT EXISTS idx_ar_analyzed_at    ON analysis_results (analyzed_at DESC);
CREATE INDEX IF NOT EXISTS idx_ar_reels_perf     ON analysis_results (reels_performance);
CREATE INDEX IF NOT EXISTS idx_ar_low_reels      ON analysis_results (low_performing_reels);
CREATE INDEX IF NOT EXISTS idx_ar_engagement     ON analysis_results (post_engagement);
CREATE INDEX IF NOT EXISTS idx_ar_monetization   ON analysis_results (monetization);
CREATE INDEX IF NOT EXISTS idx_ar_follower_count ON analysis_results (follower_count);
CREATE INDEX IF NOT EXISTS idx_ar_eng_rate       ON analysis_results (engagement_rate_pct);

CREATE OR REPLACE VIEW analysis_passed AS
SELECT
    username,
    follower_count,
    reels_performance,
    low_performing_reels,
    post_engagement,
    monetization,
    engagement_rate_pct,
    bottom10_avg_views,
    youtube_url,
    twitter_url,
    analyzed_at
FROM analysis_results
WHERE reels_performance = true
  AND low_performing_reels = true
  AND post_engagement = true
  AND error IS NULL
ORDER BY engagement_rate_pct DESC;
