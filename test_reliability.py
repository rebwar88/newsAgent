import os
import tempfile
import unittest
from unittest.mock import patch, Mock

import main


class ReliabilityTests(unittest.TestCase):
    def test_publishing_disabled_by_default(self):
        with patch.object(main, "PUBLISH_ENABLED", False), patch.object(main, "fetch_rss_news") as fetch:
            main.main()
            fetch.assert_not_called()

    def test_missing_gemini_key_never_returns_raw_news(self):
        with patch.object(main, "GEMINI_API_KEY", None):
            self.assertIsNone(main.rewrite_with_gemini("This is unverified raw source text"))

    def test_failed_telegram_delivery_returns_false(self):
        with patch.object(main, "TELEGRAM_BOT_TOKEN", "dummy"), patch.object(main, "TELEGRAM_CHAT_ID", "dummy"):
            with patch.object(main.requests, "post", return_value=Mock(status_code=200, json=lambda: {"ok": False})):
                self.assertFalse(main.send_to_telegram("News item", "https://example.com"))

    def test_failed_delivery_not_marked_seen(self):
        item = {"id": "story-1", "source": "test", "content": "This is a long enough test article for rewriting.", "link": ""}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(main, "SEEN_FILE", os.path.join(folder, "seen.json")):
                with patch.object(main, "PUBLISH_ENABLED", True), patch.object(main, "RSS_FEEDS", []):
                    with patch.object(main, "TELEGRAM_CHANNELS", []), patch.object(main, "fetch_rss_news", return_value=[item]):
                        with patch.object(main, "rewrite_with_gemini", return_value="Rewritten news"):
                            with patch.object(main, "send_to_telegram", return_value=False):
                                main.main()
                                self.assertEqual(main.load_seen_ids(), set())

    def test_corrupt_seen_file_blocks_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "seen.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("not valid json")
            with patch.object(main, "SEEN_FILE", path), patch.object(main, "PUBLISH_ENABLED", True):
                with self.assertRaises(RuntimeError):
                    main.main()


if __name__ == "__main__":
    unittest.main()
