"""Regression tests: do not publish headline-only RSS as full international news."""
from unittest.mock import patch, Mock
from datetime import datetime, timedelta, timezone
import unittest

import international_article_text as article
import international_sources as intl
import main


class ArticleEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.item = {
            "id": "intl:example",
            "international_source": True,
            "source_name": "Middle East Eye",
            "source_type": "website",
            "link": "https://www.middleeasteye.net/news/iran-iraq-report",
            "title": "Iraq and Iran regional security news",
            "content": "A short RSS description without the underlying report.",
        }

    @staticmethod
    def full_html():
        lines = [
            "Iraq's government held talks over regional security as officials considered the latest developments along the border. Government spokespeople described the meeting and said that further discussions were planned.",
            "Several officials discussed measures being prepared for affected areas. They described coordination between local administrations, while stressing that any new decision would require additional consultations.",
            "Iranian and Iraqi delegates reviewed the evolving diplomatic situation and referred to earlier public announcements. The meeting focused on specific questions raised by regional officials and their counterparts.",
            "Security officials said no immediate change to local arrangements was announced at the event. They urged residents to seek verified information instead of relying on unconfirmed social media reports.",
            "At the end of the meeting, participants described possible next steps and said further updates would be released through official channels if the discussions produced concrete measures.",
        ]
        return "<html><body><article><h1>Latest Iraq talks</h1>" + "".join(
            "<p>" + line + "</p>" for line in lines
        ) + "</article><footer>Subscribe to our newsletter</footer></body></html>"

    def response(self, html, status=200):
        result = Mock(status_code=status, headers={"Content-Type": "text/html; charset=utf-8"},
                      encoding="utf-8")
        result.iter_content.return_value = [html.encode("utf-8")]
        return result

    def test_full_article_from_approved_public_page_has_verifiable_evidence(self):
        with patch.object(article, "_robots_allowed", return_value=True) as robots:
            with patch.object(article.requests, "get", return_value=self.response(self.full_html())) as get:
                result = article.enrich_international_item(self.item)
        self.assertIsNotNone(result)
        self.assertTrue(result["international_full_evidence"])
        self.assertTrue(article.has_complete_international_evidence(result))
        self.assertGreaterEqual(result["source_word_count"], article.MIN_REPORT_WORDS)
        self.assertIn("Iraq's government held talks", result["content"])
        self.assertIn("At the end of the meeting", result["content"])
        self.assertNotIn("Subscribe to our newsletter", result["content"])
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual(robots.call_count, 1)

    def test_short_live_blog_update_is_not_misrepresented_as_full_article(self):
        html = ("<article><p>Israeli media reported that the prime minister asked "
                "the US president to consider a new attack against Iran.</p>"
                "<p>The president had not announced a decision, according to "
                "the same report, which cited unnamed officials.</p></article>")
        self.assertIsNone(article.full_story_text(html))
        with patch.object(article, "_robots_allowed", return_value=True), patch.object(
                article.requests, "get", return_value=self.response(html)):
            self.assertIsNone(article.enrich_international_item(self.item))

    def test_google_news_proxy_is_not_article_evidence(self):
        bad = dict(self.item, link="https://news.google.com/rss/articles/ABCD", source_name="Reuters")
        with patch.object(article.requests, "get") as req:
            self.assertIsNone(article.enrich_international_item(bad))
            req.assert_not_called()

    def test_restricted_publishers_are_never_scraped(self):
        for domain in ("reuters.com", "apnews.com", "bbc.com", "nytimes.com",
                       "washingtonpost.com", "cnn.com"):
            with self.subTest(domain=domain), patch.object(article.requests, "get") as req:
                bad = dict(self.item, link=f"https://www.{domain}/news/iran")
                self.assertIsNone(article.enrich_international_item(bad))
                req.assert_not_called()

    def test_non_https_port_and_redirect_are_denied(self):
        for url in ("http://www.middleeasteye.net/news/test",
                    "https://www.middleeasteye.net:8080/news/test",
                    "https://middleeasteye.net.evil.test/news/test"):
            bad = dict(self.item, link=url)
            with patch.object(article.requests, "get") as req:
                self.assertIsNone(article.enrich_international_item(bad))
                req.assert_not_called()
        with patch.object(article, "_robots_allowed", return_value=True), patch.object(
                article.requests, "get", return_value=self.response(self.full_html(), status=302)):
            self.assertIsNone(article.enrich_international_item(self.item))

    def test_robot_denial_stops_the_article_request(self):
        with patch.object(article, "_robots_allowed", return_value=False), patch.object(
                article.requests, "get") as req:
            self.assertIsNone(article.enrich_international_item(self.item))
            req.assert_not_called()

    def test_unverified_old_queued_article_is_blocked_even_if_prepared(self):
        older = dict(self.item, prepared_text={
            "title": "هەواڵێکی گرنگی نێودەوڵەتی بڵاوکرایەوە",
            "body": "ئەو هەواڵەی پێشتر لە سەرچاوەکە بڵاوکرایەوە. " * 8,
        })
        self.assertFalse(main.has_complete_international_evidence(older))
        self.assertTrue(main.has_complete_international_evidence(
            {"source_type": "telegram", "content": "normal"}))

    def test_provenance_fetched_from_rss_only_not_a_full_article(self):
        spec = intl.SOURCES[2]
        fake = {
            "title": "Iran war may trigger regional security negotiations this week",
            "link": "https://www.middleeasteye.net/news/iran-security",
            "summary": "This short RSS snippet does not contain the article's complete reporting.",
        }
        candidate = intl._item(fake, spec, datetime.now(timezone.utc))
        self.assertIsNotNone(candidate)
        self.assertFalse(article.has_complete_international_evidence(candidate))

    def test_collector_returns_only_enriched_full_items(self):
        candidate = dict(self.item)
        now = datetime.now(timezone.utc)
        with patch.object(intl, "_fetch_source", return_value=[candidate]) as fetch:
            with patch.object(intl, "enrich_international_item", return_value=None) as enrich:
                items = intl.fetch_international_news(set(), now)
        self.assertEqual(items, [])
        self.assertEqual(fetch.call_count, 9)
        self.assertEqual(enrich.call_count, 1)  # identical IDs use a 30-minute no-repeat cache

    def test_overlong_article_is_not_silently_truncated_as_complete(self):
        html = self.full_html().replace(
            "</article>", ("<p>" + "A verified event update. " * 1000 + "</p>") + "</article>"
        )
        self.assertIsNone(article.full_story_text(html))

    def test_short_source_cache_avoids_repeated_page_fetch_and_rechecks_later(self):
        now = datetime(2026, 11, 23, 5, 0, tzinfo=timezone.utc)
        state = {}
        with patch.object(intl, "_fetch_source", return_value=[self.item]):
            with patch.object(intl, "enrich_international_item", return_value=None) as enrich:
                self.assertEqual(intl.fetch_international_news(set(), now, state), [])
                self.assertEqual(
                    intl.fetch_international_news(set(), now + timedelta(minutes=5), state),
                    []
                )
                self.assertEqual(enrich.call_count, 1)
                self.assertEqual(
                    intl.fetch_international_news(set(), now + timedelta(minutes=31), state),
                    []
                )
                self.assertEqual(enrich.call_count, 2)

    def test_paywall_challenge_http_403_is_not_retried_or_bypassed(self):
        with patch.object(article, "_robots_allowed", return_value=True), patch.object(
                article.requests, "get", return_value=self.response("", status=403)) as get:
            self.assertIsNone(article.enrich_international_item(self.item))
        self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
