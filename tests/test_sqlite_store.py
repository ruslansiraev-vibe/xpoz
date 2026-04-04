import csv
import tempfile
import unittest
from pathlib import Path

from sqlite_store import (
    _existing_columns,
    _normalize_login,
    build_discovery_candidate_rows,
    connect,
    fetch_result,
    init_db,
    query_all_usernames,
    replace_discovery_candidates,
    sync_accounts_csv,
    upsert_analysis_result,
)


class TestNormalizeLogin(unittest.TestCase):
    def test_extracts_login_from_instagram_url(self):
        self.assertEqual(
            _normalize_login("https://www.instagram.com/example.creator?utm_source=qr"),
            "example.creator",
        )

    def test_extracts_login_from_reel_url(self):
        self.assertEqual(
            _normalize_login("https://instagram.com/reel/example_user/"),
            "example_user",
        )

    def test_strips_at_prefix(self):
        self.assertEqual(_normalize_login("@plain_user"), "plain_user")


class TestSyncAccountsCsv(unittest.TestCase):
    def test_sync_accounts_csv_normalizes_owner_instagram_column(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "accounts.csv"
            db_path = Path(tmpdir) / "data.db"
            with csv_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["ownerInstagram", "email", "fol_cnt"])
                writer.writeheader()
                writer.writerow(
                    {
                        "ownerInstagram": "https://www.instagram.com/creator.one?igsh=abc",
                        "email": "",
                        "fol_cnt": "5100",
                    }
                )
                writer.writerow(
                    {
                        "ownerInstagram": "http://instagram.com/creator_two",
                        "email": "",
                        "fol_cnt": "300",
                    }
                )

            conn = connect(db_path)
            try:
                imported = sync_accounts_csv(conn, str(csv_path))
                usernames = query_all_usernames(conn, limit=10, skip_analyzed=False)
            finally:
                conn.close()

            self.assertEqual(imported, 2)
            self.assertEqual(usernames, ["creator.one", "creator_two"])


class TestAnalysisResultsSchema(unittest.TestCase):
    def test_init_db_migrates_taxonomy_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            conn = connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE analysis_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL,
                        analyzed_at TEXT,
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
                        xpoz_results_used INTEGER DEFAULT 0
                    )
                    """
                )
                conn.execute("CREATE TABLE accounts (_rowid INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT)")
                conn.commit()
                init_db(conn)
                columns = set(_existing_columns(conn, "analysis_results"))
                self.assertIn("offer_type", columns)
                self.assertIn("platform_mix", columns)
                self.assertIn("bio_keywords", columns)
                self.assertIn("icp", columns)
                discovery_cols = set(_existing_columns(conn, "discovery_candidates"))
                self.assertIn("seed_username", discovery_cols)
                self.assertIn("candidate_username", discovery_cols)
            finally:
                conn.close()

    def test_upsert_round_trip_taxonomy_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data.db"
            conn = connect(db_path)
            try:
                init_db(conn)
                row_id = upsert_analysis_result(
                    conn,
                    username="creator_one",
                    record={
                        "username": "creator_one",
                        "analyzed_at": "2026-04-02T00:00:00",
                        "follower_count": 12000,
                        "posts_analyzed": 20,
                        "reels_performance": True,
                        "reels_90d_count": 7,
                        "reels_above_150pct": 5,
                        "low_performing_reels": True,
                        "bottom10_avg_views": 2200,
                        "post_engagement": True,
                        "engagement_rate_pct": 3.2,
                        "total_interactions": 1400,
                        "monetization": True,
                        "monetization_signals": ["book a call", "consulting"],
                        "monetization_reason": "Clear consulting CTA.",
                        "offer_type": "consulting",
                        "offer_type_confidence": 0.82,
                        "funnel_type": "book_call",
                        "business_model": "service_business",
                        "audience_type": "b2b",
                        "monetization_strength": "strong",
                        "platform_mix": "instagram_youtube",
                        "primary_domain": "example.com",
                        "bio_keywords": ["founder", "growth"],
                        "cta_keywords": ["book a call"],
                        "language": "en",
                        "geo_hint": "usa",
                        "youtube_url": "https://youtube.com/@creator",
                        "other_socials": {"youtube": "https://youtube.com/@creator"},
                        "error": None,
                        "llm_cost_usd": 0.02,
                        "xpoz_results_used": 22,
                        "icp": "ICP2",
                    },
                )
                item = fetch_result(conn, row_id)
            finally:
                conn.close()

            self.assertIsNotNone(item)
            self.assertEqual(item["offer_type"], "consulting")
            self.assertEqual(item["funnel_type"], "book_call")
            self.assertEqual(item["icp"], "ICP2")
            self.assertEqual(item["bio_keywords"], ["founder", "growth"])
            self.assertEqual(item["cta_keywords"], ["book a call"])
            self.assertTrue(item["monetization"])


class TestDiscoveryLayer(unittest.TestCase):
    def test_build_discovery_candidates_uses_only_strong_seed_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data.db"
            conn = connect(db_path)
            try:
                init_db(conn)
                upsert_analysis_result(
                    conn,
                    username="good_seed",
                    record={
                        "username": "good_seed",
                        "analyzed_at": "2026-04-02T00:00:00",
                        "follower_count": 25000,
                        "monetization": True,
                        "offer_type": "coaching",
                        "funnel_type": "book_call",
                        "business_model": "education",
                        "audience_type": "b2b",
                        "monetization_strength": "strong",
                        "platform_mix": "multi_channel",
                        "primary_domain": "goodseed.com",
                        "bio_keywords": ["founder growth"],
                        "cta_keywords": ["book a call"],
                        "icp": "ICP1",
                        "error": None,
                    },
                )
                upsert_analysis_result(
                    conn,
                    username="weak_seed",
                    record={
                        "username": "weak_seed",
                        "analyzed_at": "2026-04-02T00:00:00",
                        "follower_count": 30000,
                        "monetization": True,
                        "offer_type": "unknown",
                        "monetization_strength": "weak",
                        "error": None,
                    },
                )

                def fake_search(query: str, limit: int):
                    return [{"username": f"{query.replace(' ', '_')}_candidate", "confidence": 0.9}]

                seeds, rows = build_discovery_candidate_rows(
                    conn,
                    search_provider=fake_search,
                    seed_limit=10,
                    per_query_limit=2,
                    min_followers=5000,
                )
                written = replace_discovery_candidates(conn, rows)
            finally:
                conn.close()

            self.assertEqual(len(seeds), 1)
            self.assertEqual(seeds[0]["username"], "good_seed")
            self.assertGreaterEqual(written, 1)
            self.assertTrue(any(row["seed_username"] == "good_seed" for row in rows))
            self.assertTrue(all(row["seed_username"] != "weak_seed" for row in rows))


if __name__ == "__main__":
    unittest.main()
