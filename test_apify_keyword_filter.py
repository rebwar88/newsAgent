"""Opt-in filtered Apify feed, with no live Apify requests in tests."""
from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import apify_facebook as af


class FilteredApifyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
        self.pages = [f"https://www.facebook.com/page-{i}" for i in range(50)]
        self.keywords = ["بەپەلە", "مووچە", "ڕووداوی هاتوچۆ"]
        self.options = (
            patch.object(af, "APIFY_TOKEN", "test-token"),
            patch.object(af, "APIFY_FILTER_ENABLED", True),
            patch.object(af, "FACEBOOK_PAGE_URLS", self.pages),
            patch.object(af, "APIFY_PAGE_BATCH_SIZE", 10),
            patch.object(af, "APIFY_MAX_ITEMS_PER_RUN", 20),
            patch.object(af, "APIFY_MAX_CHARGE_USD", 0.05),
            patch.object(af, "APIFY_RESULTS_LIMIT", 2),
            patch.object(af, "load_news_keywords", return_value=self.keywords),
        )
        for entry in self.options:
            entry.start()
            self.addCleanup(entry.stop)

    def _good_response(self, rows):
        return Mock(ok=True, status_code=201, json=lambda: rows)

    def test_request_contains_keywords_prior_to_actor_run_and_spend_caps(self):
        state = {}
        with patch.object(af.requests, "post", return_value=self._good_response([])) as post:
            self.assertEqual(af.fetch_facebook_via_apify(set(), state, self.now), [])
        self.assertIn("topapi~facebook-page-posts-scraper", post.call_args.args[0])
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["keywordFilter"], "بەپەلە, مووچە, ڕووداوی هاتوچۆ")
        self.assertTrue(payload["monitorMode"])
        self.assertFalse(payload["includePostDetails"])
        self.assertFalse(payload["captionText"])
        self.assertEqual(payload["maxPostsPerPage"], 2)
        self.assertEqual(payload["maxItems"], 20)
        self.assertEqual(payload["startUrls"], self.pages[:10])
        self.assertEqual(post.call_args.kwargs["params"]["maxItems"], 20)
        self.assertEqual(post.call_args.kwargs["params"]["maxTotalChargeUsd"], 0.05)
        self.assertEqual(state["apify_filtered_cursor"], 10)

    def test_fifty_pages_rotate_without_repeatedly_favoring_first_pages(self):
        state = {}
        starts = []
        with patch.object(af.requests, "post", return_value=self._good_response([])) as post:
            for k in range(5):
                self.assertEqual(af.fetch_facebook_via_apify(set(), state, self.now + timedelta(hours=3*k)), [])
                starts.append(post.call_args.kwargs["json"]["startUrls"])
        self.assertEqual([p for batch in starts for p in batch], self.pages)
        self.assertEqual(state["apify_filtered_cursor"], 0)

    def test_no_rapid_retry_after_failure_and_cursor_stays_put(self):
        state = {}
        with patch.object(af.requests, "post", return_value=Mock(ok=False, status_code=401)) as post:
            self.assertEqual(af.fetch_facebook_via_apify(set(), state, self.now), [])
            self.assertEqual(af.fetch_facebook_via_apify(set(), state, self.now + timedelta(minutes=5)), [])
            self.assertEqual(post.call_count, 1)
        self.assertNotIn("apify_filtered_cursor", state)
        self.assertIn("last_apify_at", state)

    def test_empty_keyword_list_never_triggers_paid_scrape(self):
        with patch.object(af, "load_news_keywords", return_value=[]):
            with patch.object(af.requests, "post") as post:
                self.assertEqual(af.fetch_facebook_via_apify(set(), {}, self.now), [])
                post.assert_not_called()

    def test_topapi_record_date_name_url_images_and_legacy_ids_are_preserved(self):
        row = {
            "post_id": "12345",
            "url": "https://www.facebook.com/page-0/posts/12345",
            "message": "بەپەلە: ڕاگەیاندنێکی گرنگ لە هەولێر لەسەر مووچەکانی فەرمانبەران",
            "published_at": "2026-11-22T11:00:00Z",
            "source_name": "پەیجی هەواڵ",
            "input_url": self.pages[0],
            "images": [{"uri": "https://fbcdn.example/1.jpg"}],
        }
        with patch.object(af.requests, "post", return_value=self._good_response([row])):
            items = af.fetch_facebook_via_apify(set(), {}, self.now)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "fb:12345")
        self.assertEqual(items[0]["source_name"], "پەیجی هەواڵ")
        self.assertEqual(items[0]["link"], row["url"])
        self.assertEqual(items[0]["images"], ["https://fbcdn.example/1.jpg"])
        self.assertTrue(items[0]["source_published_at"].startswith("2026-11-22T11:00"))

    def test_seen_facebook_item_is_not_sent_again(self):
        row = {"post_id": "101", "message": "بەپەلە: هەواڵێکی ورد لە هەرێمی کوردستان ڕاگەیەندرا",
               "input_url": self.pages[0], "published_at": "2026-11-22T11:00:00Z"}
        with patch.object(af.requests, "post", return_value=self._good_response([row])):
            self.assertEqual(af.fetch_facebook_via_apify({"fb:101"}, {}, self.now), [])

    def test_malformed_result_fails_closed_without_advance(self):
        state = {}
        with patch.object(af.requests, "post", return_value=self._good_response({"error": "bad actor"})):
            self.assertEqual(af.fetch_facebook_via_apify(set(), state, self.now), [])
        self.assertNotIn("apify_filtered_cursor", state)

    def test_json_keywords_file_keeps_user_selected_list(self):
        real = Path(__file__).with_name("facebook_news_keywords.json")
        data = json.loads(real.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 16)
        self.assertIn("جوتیار", data)
        self.assertIn("کۆمەڵی دادگەریی", data)

    def test_invalid_keywords_config_skips_actor(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broken.json"
            path.write_text("{", encoding="utf-8")
            with patch.object(af, "APIFY_KEYWORDS_FILE", path), patch.object(af, "load_news_keywords", wraps=af.load_news_keywords):
                self.assertEqual(af.load_news_keywords(), [])


if __name__ == "__main__":
    unittest.main()
