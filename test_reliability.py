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


    EN = "Iran says it is ready for talks with European countries over its nuclear programme and all related issues today."
    CKB = "ئێران ڕایگەیاند کە ئامادەیە بۆ دانوستان لەگەڵ وڵاتانی ئەوروپا دەربارەی پرۆگرامی ناوکی و هەموو پرسە پەیوەندیدارەکان لە ئەمڕۆدا."

    def _resp(self, text, ok=True):
        return Mock(ok=ok, status_code=200 if ok else 503, json=lambda: [[[text, "src", None, None, 1]], None, "en"])

    def test_english_news_is_translated_when_gemini_unavailable(self):
        with patch.object(main.requests, "post", return_value=self._resp(self.CKB)):
            result = main.translate_without_gemini({"title": "", "content": self.EN})
        self.assertIsNotNone(result)
        self.assertGreaterEqual(len(result["body"]), 60)
        self.assertNotRegex(result["title"] + result["body"], r"[A-Za-z]{4,}")

    def test_translation_service_failure_skips_item(self):
        with patch.object(main.requests, "post", return_value=self._resp("", ok=False)), patch.object(main.time, "sleep"):
            self.assertIsNone(main.translate_without_gemini({"title": "", "content": self.EN}))

    def test_untranslated_output_is_rejected(self):
        with patch.object(main.requests, "post", return_value=self._resp(self.EN)):
            self.assertIsNone(main.translate_without_gemini({"title": "", "content": self.EN}))

    def test_translation_fallback_can_be_disabled(self):
        with patch.object(main, "TRANSLATE_FALLBACK_ENABLED", False), patch.object(main.requests, "post") as post:
            self.assertIsNone(main.translate_without_gemini({"title": "", "content": self.EN}))
            post.assert_not_called()

    def test_english_item_published_via_translation_when_gemini_fails(self):
        item = {"id": "en-1", "source": "test", "title": "", "content": self.EN, "link": ""}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(main, "SEEN_FILE", os.path.join(folder, "seen.json")), patch.object(main, "PUBLISH_STATE_FILE", os.path.join(folder, "state.json")):
                with patch.object(main, "PUBLISH_ENABLED", True), patch.object(main, "TELEGRAM_CHANNELS", []), patch.object(main, "fetch_rss_news", return_value=[item]):
                    with patch.object(main, "rewrite_with_gemini", return_value=None), patch.object(main.requests, "post", return_value=self._resp(self.CKB)):
                        with patch.object(main, "send_to_telegram", return_value=True) as send:
                            main.main()
                            send.assert_called_once()
                            self.assertEqual(main.load_seen_ids(), {"en-1"})


    def test_backlog_flush_publishes_everything_once(self):
        ckb = "ئێران ڕایگەیاند کە ئامادەیە بۆ دانوستان لەگەڵ وڵاتانی ئەوروپا دەربارەی پرۆگرامی ناوکی و هەموو پرسە پەیوەندیدارەکان لە ئەمڕۆدا."
        items = [{"id": f"b{i}", "source": "t", "title": "", "content": ckb, "link": ""} for i in range(4)]
        with tempfile.TemporaryDirectory() as folder:
            flag = os.path.join(folder, "flush.json")
            with open(flag, "w", encoding="utf-8") as f:
                f.write('{"id": "flush-1"}')
            with patch.object(main, "SEEN_FILE", os.path.join(folder, "seen.json")), patch.object(main, "PUBLISH_STATE_FILE", os.path.join(folder, "state.json")), patch.object(main, "FLUSH_FILE", flag):
                with patch.object(main, "PUBLISH_ENABLED", True), patch.object(main, "TELEGRAM_CHANNELS", []), patch.object(main, "fetch_rss_news", return_value=items):
                    with patch.object(main, "rewrite_with_gemini", return_value=None), patch.object(main.time, "sleep"):
                        with patch.object(main, "send_to_telegram", return_value=True) as send:
                            main.main()
                            self.assertEqual(send.call_count, 4)
                            self.assertEqual(main.load_seen_ids(), {"b0", "b1", "b2", "b3"})
                            self.assertEqual(main.load_publication_state()["flushed_id"], "flush-1")
                            main.main()  # same flag id must not flush again
                            self.assertEqual(send.call_count, 4)


    def test_extracts_photo_from_telegram_preview_html(self):
        from bs4 import BeautifulSoup
        html_doc = '<div class="tgme_widget_message"><a class="tgme_widget_message_photo_wrap" style="width:800px;background-image:url(\'https://cdn.example.org/file/a.jpg\')"></a></div>'
        msg = BeautifulSoup(html_doc, "html.parser").find("div")
        self.assertEqual(main.extract_telegram_image(msg), "https://cdn.example.org/file/a.jpg")
        self.assertEqual(main.extract_telegram_image(BeautifulSoup("<div></div>", "html.parser").find("div")), "")

    def _tg_ok(self):
        return Mock(ok=True, status_code=200, json=lambda: {"ok": True})

    def _send_with_image(self, message, post_results):
        image = Mock(ok=True, status_code=200, headers={"Content-Type": "image/jpeg"}, content=b"x" * 5000)
        with patch.object(main, "TELEGRAM_BOT_TOKEN", "t"), patch.object(main, "TELEGRAM_CHAT_ID", "c"):
            with patch.object(main.requests, "get", return_value=image), patch.object(main.requests, "post", side_effect=post_results) as post:
                result = main.send_to_telegram(message, image_url="https://cdn.example.org/a.jpg")
        return result, [c.args[0].rsplit("/", 1)[-1] for c in post.call_args_list]

    def test_short_article_sent_as_photo_with_caption(self):
        result, calls = self._send_with_image({"title": "سەردێڕی تاقیکردنەوە", "body": "دەقی کورت"}, [self._tg_ok()])
        self.assertTrue(result)
        self.assertEqual(calls, ["sendPhoto"])

    def test_long_article_sends_photo_then_text(self):
        result, calls = self._send_with_image({"title": "سەردێڕی تاقیکردنەوە", "body": "ب" * 1500}, [self._tg_ok(), self._tg_ok()])
        self.assertTrue(result)
        self.assertEqual(calls, ["sendPhoto", "sendMessage"])

    def test_rejected_photo_falls_back_to_text(self):
        bad = Mock(ok=False, status_code=400, json=lambda: {"description": "bad image"})
        result, calls = self._send_with_image({"title": "سەردێڕی تاقیکردنەوە", "body": "دەقی کورت"}, [bad, self._tg_ok()])
        self.assertTrue(result)
        self.assertEqual(calls, ["sendPhoto", "sendMessage"])

    def test_non_image_download_is_ignored(self):
        page = Mock(ok=True, status_code=200, headers={"Content-Type": "text/html"}, content=b"x" * 5000)
        with patch.object(main.requests, "get", return_value=page):
            self.assertIsNone(main.download_image("https://example.org/a"))


if __name__ == "__main__":
    unittest.main()
