from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import patch, Mock

import main


class ReliabilityTests(unittest.TestCase):
    def test_openrouter_requires_key(self):
        with patch.object(main, "OPENROUTER_API_KEY", None):
            with patch.object(main.requests, "post") as post:
                self.assertIsNone(main.rewrite_with_openrouter("sample"))
                post.assert_not_called()

    def test_openrouter_success_sorani(self):
        payload = {"choices": [{"message": {"content": '{"title":"هەواڵێکی نوێ لە هەولێر", "body":"ئەمڕۆ لە هەولێر هەواڵێکی نوێ ڕاگەیەندرا. بەرپرسان ڕوونیان کردەوە کە ڕووداوەکە لەژێر چاودێریدایە و وردەکارییەکان دواتر بڵاو دەکرێنەوە."}'}}]}
        with patch.object(main, "OPENROUTER_API_KEY", "test"), patch.object(main, "OPENROUTER_MODELS", ["openrouter/free"]):
            with patch.object(main.requests, "post", return_value=Mock(ok=True, json=lambda: payload)) as post:
                result = main.rewrite_with_openrouter("خبر جدید")
                self.assertIsNotNone(result)
                self.assertIn("هەواڵ", result["title"])
                self.assertEqual(post.call_count, 1)

    def test_openrouter_rejects_untranslated_persian(self):
        payload = {"choices": [{"message": {"content": '{"title":"خبر فوری از تهران", "body":"مقامات امروز اعلام کردند که جلسه مهمی درباره وضعیت منطقه برگزار شده است و جزئیات بیشتری به زودی منتشر خواهد شد."}'}}]}
        with patch.object(main, "OPENROUTER_API_KEY", "test"), patch.object(main, "OPENROUTER_MODELS", ["openrouter/free"]):
            with patch.object(main.requests, "post", return_value=Mock(ok=True, json=lambda: payload)):
                self.assertIsNone(main.rewrite_with_openrouter("خبر جدید"))

    def test_gemini_prepared_article_selected_first(self):
        articles = [
            {"id": "fallback", "source": "a", "content": "text"},
            {"id": "gemini", "source": "b", "content": "text", "prepared_method": "gemini"},
        ]
        self.assertEqual(main.choose_pending(articles, {}, set())["id"], "gemini")

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
            with patch.object(main, "SEEN_FILE", os.path.join(folder, "seen.json")), patch.object(main, "PUBLISH_STATE_FILE", os.path.join(folder, "state.json")):
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
            with patch.object(main, "SEEN_FILE", path), patch.object(main, "PUBLISH_STATE_FILE", os.path.join(folder, "state.json")), patch.object(main, "PUBLISH_ENABLED", True):
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


    def test_extracts_small_video_url(self):
        from bs4 import BeautifulSoup
        msg = BeautifulSoup('<div><video class="tgme_widget_message_video" src="https://cdn.example.org/v.mp4"></video></div>', "html.parser").find("div")
        self.assertEqual(main.extract_telegram_video(msg), "https://cdn.example.org/v.mp4")
        self.assertEqual(main.extract_telegram_video(BeautifulSoup("<div></div>", "html.parser").find("div")), "")

    def test_video_is_uploaded_with_sendvideo(self):
        video = Mock(ok=True, status_code=200, headers={"Content-Type": "video/mp4"}, iter_content=lambda chunk_size: [b"v" * 5000])
        with patch.object(main, "TELEGRAM_BOT_TOKEN", "t"), patch.object(main, "TELEGRAM_CHAT_ID", "c"):
            with patch.object(main.requests, "get", return_value=video), patch.object(main.requests, "post", return_value=self._tg_ok()) as post:
                result = main.send_to_telegram({"title": "سەردێڕی تاقیکردنەوە", "body": "دەقی کورت"}, video_url="https://cdn.example.org/v.mp4")
        self.assertTrue(result)
        self.assertTrue(post.call_args.args[0].endswith("/sendVideo"))

    def test_oversized_video_is_skipped(self):
        big = Mock(ok=True, status_code=200, headers={"Content-Type": "video/mp4"}, iter_content=lambda chunk_size: [b"v" * 30_000_000, b"v" * 30_000_000])
        with patch.object(main.requests, "get", return_value=big):
            self.assertIsNone(main.download_video("https://cdn.example.org/v.mp4"))


    def test_extracts_video_from_telegram_preview_html(self):
        from bs4 import BeautifulSoup
        doc = '<div class="m"><video class="tgme_widget_message_video js-message_video" src="https://cdn.example.org/v.mp4"></video></div>'
        self.assertEqual(main.extract_telegram_video(BeautifulSoup(doc, "html.parser").find("div")), "https://cdn.example.org/v.mp4")
        self.assertEqual(main.extract_telegram_video(BeautifulSoup("<div></div>", "html.parser").find("div")), "")

    def _video_response(self, size=6000, ctype="video/mp4", length=None):
        headers = {"Content-Type": ctype}
        if length is not None:
            headers["Content-Length"] = str(length)
        return Mock(ok=True, status_code=200, headers=headers, iter_content=lambda n: iter([b"v" * size]))

    def test_video_is_sent_with_sendvideo(self):
        with patch.object(main, "TELEGRAM_BOT_TOKEN", "t"), patch.object(main, "TELEGRAM_CHAT_ID", "c"):
            with patch.object(main.requests, "get", return_value=self._video_response()), patch.object(main.requests, "post", side_effect=[self._tg_ok()]) as post:
                result = main.send_to_telegram({"title": "سەردێڕی تاقیکردنەوە", "body": "دەقی کورت"}, video_url="https://cdn.example.org/v.mp4")
        self.assertTrue(result)
        self.assertEqual([c.args[0].rsplit("/", 1)[-1] for c in post.call_args_list], ["sendVideo"])

    def test_oversized_video_falls_back_to_photo(self):
        def fake_get(url, **kwargs):
            if url.endswith(".mp4"):
                return self._video_response(length=60_000_000)
            return Mock(ok=True, status_code=200, headers={"Content-Type": "image/jpeg"}, content=b"x" * 5000)
        with patch.object(main, "TELEGRAM_BOT_TOKEN", "t"), patch.object(main, "TELEGRAM_CHAT_ID", "c"):
            with patch.object(main.requests, "get", side_effect=fake_get), patch.object(main.requests, "post", side_effect=[self._tg_ok()]) as post:
                result = main.send_to_telegram({"title": "سەردێڕی تاقیکردنەوە", "body": "دەقی کورت"}, image_url="https://cdn.example.org/a.jpg", video_url="https://cdn.example.org/v.mp4")
        self.assertTrue(result)
        self.assertEqual([c.args[0].rsplit("/", 1)[-1] for c in post.call_args_list], ["sendPhoto"])

    def test_video_over_limit_without_content_length_is_dropped(self):
        big = Mock(ok=True, status_code=200, headers={"Content-Type": "video/mp4"}, iter_content=lambda n: iter([b"v" * 30_000_000, b"v" * 30_000_000]))
        with patch.object(main.requests, "get", return_value=big):
            self.assertIsNone(main.download_video("https://cdn.example.org/v.mp4"))

    def test_rejected_video_falls_back_to_text(self):
        bad = Mock(ok=False, status_code=400, json=lambda: {"description": "bad video"})
        with patch.object(main, "TELEGRAM_BOT_TOKEN", "t"), patch.object(main, "TELEGRAM_CHAT_ID", "c"):
            with patch.object(main.requests, "get", return_value=self._video_response()), patch.object(main.requests, "post", side_effect=[bad, self._tg_ok()]) as post:
                result = main.send_to_telegram({"title": "سەردێڕی تاقیکردنەوە", "body": "دەقی کورت"}, video_url="https://cdn.example.org/v.mp4")
        self.assertTrue(result)
        self.assertEqual([c.args[0].rsplit("/", 1)[-1] for c in post.call_args_list], ["sendVideo", "sendMessage"])


    @staticmethod
    def _box(kind, payload):
        return (len(payload) + 8).to_bytes(4, "big") + kind + payload

    def _fake_mp4(self, width, height, rotated=False, duration=12, timescale=1000):
        import struct
        mvhd = self._box(b"mvhd", b"\x00\x00\x00\x00" + b"\x00" * 8 + timescale.to_bytes(4, "big") + (duration * timescale).to_bytes(4, "big") + b"\x00" * 80)
        matrix = struct.pack(">9i", 0 if rotated else 0x10000, 0x10000 if rotated else 0, 0, -0x10000 if rotated else 0, 0 if rotated else 0x10000, 0, 0, 0, 0x40000000)
        tkhd = self._box(b"tkhd", b"\x00\x00\x00\x07" + b"\x00" * 20 + b"\x00" * 16 + matrix + (width << 16).to_bytes(4, "big") + (height << 16).to_bytes(4, "big"))
        hdlr = self._box(b"hdlr", b"\x00" * 8 + b"vide" + b"\x00" * 12)
        trak = self._box(b"trak", tkhd + self._box(b"mdia", hdlr))
        return self._box(b"ftyp", b"isom" + b"\x00" * 8) + self._box(b"mdat", b"\x00" * 6000) + self._box(b"moov", mvhd + trak)

    def test_reads_mp4_size_and_duration(self):
        self.assertEqual(main.read_mp4_info(self._fake_mp4(1280, 720)), (1280, 720, 12))

    def test_rotated_mp4_swaps_size(self):
        self.assertEqual(main.read_mp4_info(self._fake_mp4(1920, 1080, rotated=True)), (1080, 1920, 12))

    def test_unreadable_video_has_no_info(self):
        self.assertIsNone(main.read_mp4_info(b"not a video" * 100))

    def test_sendvideo_includes_real_dimensions(self):
        video = Mock(ok=True, status_code=200, headers={"Content-Type": "video/mp4"}, iter_content=lambda n: iter([self._fake_mp4(720, 1280)]))
        with patch.object(main, "TELEGRAM_BOT_TOKEN", "t"), patch.object(main, "TELEGRAM_CHAT_ID", "c"):
            with patch.object(main.requests, "get", return_value=video), patch.object(main.requests, "post", side_effect=[self._tg_ok()]) as post:
                main.send_to_telegram({"title": "سەردێڕی تاقیکردنەوە", "body": "دەقی کورت"}, video_url="https://cdn.example.org/v.mp4")
        sent = post.call_args.kwargs["data"]
        self.assertEqual((sent["width"], sent["height"], sent["duration"]), ("720", "1280", "12"))


    def test_extracts_all_photos_of_an_album(self):
        from bs4 import BeautifulSoup
        doc = "<div>" + "".join(f'<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example.org/{n}.jpg\')"></a>' for n in "abc") + "</div>"
        urls = main.extract_telegram_images(BeautifulSoup(doc, "html.parser").find("div"))
        self.assertEqual(urls, ["https://cdn.example.org/a.jpg", "https://cdn.example.org/b.jpg", "https://cdn.example.org/c.jpg"])

    def _album_send(self, message, post_results):
        image = Mock(ok=True, status_code=200, headers={"Content-Type": "image/jpeg"}, content=b"x" * 5000)
        urls = [f"https://cdn.example.org/{n}.jpg" for n in "abc"]
        with patch.object(main, "TELEGRAM_BOT_TOKEN", "t"), patch.object(main, "TELEGRAM_CHAT_ID", "c"):
            with patch.object(main.requests, "get", return_value=image), patch.object(main.requests, "post", side_effect=post_results) as post:
                result = main.send_to_telegram(message, image_urls=urls)
        return result, post

    def test_multiple_photos_are_sent_as_one_album(self):
        import json as _json
        result, post = self._album_send({"title": "سەردێڕی تاقیکردنەوە", "body": "دەقی کورت"}, [self._tg_ok()])
        self.assertTrue(result)
        self.assertEqual([c.args[0].rsplit("/", 1)[-1] for c in post.call_args_list], ["sendMediaGroup"])
        sent = post.call_args.kwargs
        media = _json.loads(sent["data"]["media"])
        self.assertEqual(len(media), 3)
        self.assertIn("caption", media[0])
        self.assertNotIn("caption", media[1])
        self.assertEqual(sorted(sent["files"]), ["photo0", "photo1", "photo2"])

    def test_long_article_album_then_text(self):
        result, post = self._album_send({"title": "سەردێڕی تاقیکردنەوە", "body": "ب" * 1500}, [self._tg_ok(), self._tg_ok()])
        self.assertTrue(result)
        self.assertEqual([c.args[0].rsplit("/", 1)[-1] for c in post.call_args_list], ["sendMediaGroup", "sendMessage"])

    def test_rejected_album_falls_back_to_single_photo(self):
        bad = Mock(ok=False, status_code=400, json=lambda: {"description": "bad album"})
        result, post = self._album_send({"title": "سەردێڕی تاقیکردنەوە", "body": "دەقی کورت"}, [bad, self._tg_ok()])
        self.assertTrue(result)
        self.assertEqual([c.args[0].rsplit("/", 1)[-1] for c in post.call_args_list], ["sendMediaGroup", "sendPhoto"])


    def test_apify_posts_become_news_items(self):
        import apify_facebook as af
        now = datetime.now(timezone.utc)
        rows = [
            {"postId": "1", "postUrl": "https://www.facebook.com/Rudaw.net/posts/1", "text": "ئێران ڕایگەیاند کە ئامادەیە بۆ دانوستان لەگەڵ وڵاتانی ئەوروپا", "time": now.isoformat(),
             "media": [{"photo_image": {"uri": "https://scontent.example/a.jpg"}}, {"thumbnail": "https://scontent.example/b.jpg"}], "inputUrl": "https://www.facebook.com/Rudaw.net"},
            {"postId": "2", "text": "کورت", "time": now.isoformat()},
            {"postId": "3", "text": "پۆستێکی کۆن کە زیاتر لە دوازدە کاتژمێر لەمەوپێش بڵاوکراوەتەوە", "time": (now - timedelta(hours=30)).isoformat()},
        ]
        with patch.object(af, "APIFY_TOKEN", "x"), patch.object(af.requests, "post", return_value=Mock(ok=True, status_code=201, json=lambda: rows)):
            state = {}
            items = af.fetch_facebook_via_apify(set(), state, now)
        self.assertEqual([i["id"] for i in items], ["fb:1"])
        self.assertEqual(items[0]["images"], ["https://scontent.example/a.jpg", "https://scontent.example/b.jpg"])
        self.assertIn("last_apify_at", state)

    def test_apify_respects_interval_and_missing_token(self):
        import apify_facebook as af
        now = datetime.now(timezone.utc)
        with patch.object(af, "APIFY_TOKEN", ""), patch.object(af.requests, "post") as post:
            self.assertEqual(af.fetch_facebook_via_apify(set(), {}, now), [])
            post.assert_not_called()
        with patch.object(af, "APIFY_TOKEN", "x"), patch.object(af.requests, "post") as post:
            self.assertEqual(af.fetch_facebook_via_apify(set(), {"last_apify_at": (now - timedelta(minutes=5)).isoformat()}, now), [])
            post.assert_not_called()

    def test_apify_failure_still_blocks_immediate_retry(self):
        import apify_facebook as af
        now = datetime.now(timezone.utc)
        state = {}
        with patch.object(af, "APIFY_TOKEN", "x"), patch.object(af.requests, "post", return_value=Mock(ok=False, status_code=402)):
            self.assertEqual(af.fetch_facebook_via_apify(set(), state, now), [])
        self.assertFalse(af.apify_slot_available(state, now + timedelta(minutes=5)))


    def test_prune_pending_drops_stale_and_blocked_items(self):
        now = datetime.now(timezone.utc)
        def item(item_id, hours_ago, stage):
            return {"id": item_id, "progress": {"received_at": (now - timedelta(hours=hours_ago)).isoformat(), "stage": stage}}
        state = {"pending_items": [item("old-blocked", 2, "blocked_content"), item("fresh-blocked", 0.2, "blocked_content"),
                                   item("stale", 7, "waiting_turn"), item("fresh", 1, "waiting_turn")]}
        seen = set()
        self.assertEqual(main.prune_pending(state, seen, now), 2)
        self.assertEqual([i["id"] for i in state["pending_items"]], ["fresh-blocked", "fresh"])
        self.assertEqual(seen, {"old-blocked", "stale"})


    def test_publishing_interval_shrinks_as_the_queue_grows(self):
        minutes = lambda n: main.adaptive_interval(n).total_seconds() / 60
        self.assertEqual(minutes(1), 5)
        self.assertEqual(minutes(4), 5)
        self.assertEqual(minutes(5), 4)
        self.assertAlmostEqual(minutes(10), 2)
        self.assertEqual(minutes(20), 1)
        self.assertEqual(minutes(60), 1)
        self.assertGreaterEqual(minutes(7), minutes(8))


if __name__ == "__main__":
    unittest.main()
