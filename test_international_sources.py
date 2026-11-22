"""Topic, publisher provenance and reliability tests. All network calls mocked."""
import calendar
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import feedparser
import international_sources as intl
import main


class InternationalNewsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc)

    def entry(self, url="https://www.middleeasteye.net/news/iraq-report", title=None, body=None,
              days=0, source=None):
        title = title or "Iraq government announces new security measures near the border"
        body = body or (
            "Iraq's security authorities said new checkpoints would be deployed "
            "near the northern border following a reported security incident in the region."
        )
        item = feedparser.FeedParserDict({
            "link": url, "title": title, "summary": body,
            "published_parsed": (self.now - timedelta(days=days)).timetuple(),
        })
        if source:
            item["source"] = feedparser.FeedParserDict(source)
        return item

    def test_nine_explicit_desks(self):
        self.assertEqual(
            [s["name"] for s in intl.SOURCES],
            ["The New York Times", "The Washington Post", "Middle East Eye",
             "Reuters", "Associated Press", "CNN", "BBC Persian",
             "BBC Arabic", "BBC English"]
        )
        self.assertEqual(len({s["name"] for s in intl.SOURCES}), 9)

    def test_iraq_in_multiple_languages(self):
        samples = [
            "Iraq parliament approves new spending measure",
            "Kurdistan government issues official statement",
            "عێراق بڕیارێکی نوێی دەرکرد",
            "العراق يعلن عن تدابير جديدة",
            "وزارت خارجه عراق بیانیه صادر کرد",
            "Explosions reported in Erbil",
        ]
        for title in samples:
            with self.subTest(title=title):
                self.assertTrue(intl.is_relevant(title))

    def test_iran_war_multi_language(self):
        for title in [
            "Iran hit by new missile strikes",
            "Tehran negotiations on nuclear sanctions resume",
            "شەڕی ئێران و هێرشەکانی ئەمریکا",
            "جنگ ایران با اسرائیل ادامه دارد",
            "إيران تناقش وقف إطلاق النار مع أمريكا",
            "Iranian forces and the Strait of Hormuz security crisis",
        ]:
            with self.subTest(title=title):
                self.assertTrue(intl.is_relevant(title))

    def test_unrelated_news_and_general_iran_stories_excluded(self):
        for title in [
            "World Cup draw announced in Europe",
            "Iranian football team celebrates its victory",
            "Iran's art museum welcomes visitors",
            "Oil prices rise in Australia",
            "Washington Post introduces a podcast",
        ]:
            with self.subTest(title=title):
                self.assertFalse(intl.is_relevant(title))

    def test_official_rss_item_attribution_and_link_preserved(self):
        spec = intl.SOURCES[2]
        row = self.entry()
        row["author"] = "Editor Example"
        item = intl._item(row, spec, self.now)
        self.assertIsNotNone(item)
        self.assertEqual(item["source_name"], "Middle East Eye")
        self.assertEqual(item["source_byline"], "Editor Example")
        self.assertEqual(item["link"], row["link"])
        self.assertTrue(item["id"].startswith("intl:"))
        self.assertTrue(item["international_source"])
        label = main.publication_credit(item)
        self.assertIn("Middle East Eye", label)
        self.assertIn(row["link"], label)
        self.assertIn("Editor Example", label)

    def test_wrong_domain_is_not_misattributed_as_publisher(self):
        spec = intl.SOURCES[0]
        item = self.entry(url="https://fake-nytimes.com/news/iraq")
        self.assertIsNone(intl._item(item, spec, self.now))
        item["link"] = "https://nytimes.com.attacker.net/iraq"
        self.assertIsNone(intl._item(item, spec, self.now))

    def test_reuters_google_news_requires_verified_source_href(self):
        spec = next(x for x in intl.SOURCES if x["name"] == "Reuters")
        url = "https://news.google.com/rss/articles/ABCDEFGHIJ"
        title = "Iraq reports new border security deployments after latest regional tension"
        no_source = self.entry(url=url, title=title)
        self.assertIsNone(intl._item(no_source, spec, self.now))
        bad = self.entry(url=url, title=title,
                         source={"href": "https://www.example.com", "title": "Reuters"})
        self.assertIsNone(intl._item(bad, spec, self.now))
        valid = self.entry(url=url, title=title,
                           source={"href": "https://www.reuters.com", "title": "Reuters"})
        self.assertIsNotNone(intl._item(valid, spec, self.now))

    def test_short_google_news_title_does_not_become_fake_full_story(self):
        spec = next(x for x in intl.SOURCES if x["name"] == "CNN")
        entry = self.entry(
            url="https://news.google.com/rss/articles/TEST",
            title="Iran attacks - CNN",
            source={"href": "https://cnn.com", "title": "CNN"})
        self.assertIsNone(intl._item(entry, spec, self.now))

    def test_stale_article_and_future_article_are_skipped(self):
        spec = intl.SOURCES[2]
        old = self.entry(days=4)
        self.assertIsNone(intl._item(old, spec, self.now))
        future = self.entry()
        future["published_parsed"] = (self.now + timedelta(days=1)).timetuple()
        self.assertIsNone(intl._item(future, spec, self.now))

    def test_feed_fetch_uses_https_timeout_and_limits_to_four(self):
        spec = intl.SOURCES[2]
        rows = [self.entry(url=f"https://www.middleeasteye.net/news/iraq-{i}") for i in range(15)]
        rss = SimpleNamespace(entries=rows)
        with patch.object(intl.requests, "get", return_value=Mock(status_code=200, content=b"<xml/>")) as get:
            with patch.object(intl.feedparser, "parse", return_value=rss):
                items = intl._fetch_source(spec, self.now, set())
        self.assertEqual(len(items), 4)
        self.assertEqual(get.call_args.kwargs["timeout"], (4, 9))

    def test_source_failure_does_not_block_others(self):
        with patch.object(intl.requests, "get", side_effect=intl.requests.Timeout()):
            items = intl.fetch_international_news(set(), self.now)
        self.assertEqual(items, [])

    def test_summary_html_is_cleaned_not_downloaded(self):
        entry = self.entry(body="<p>Iraq's <b>government</b> announced a new border "
                                      "security deployment following overnight incidents.</p><script>leak()</script>")
        result = intl._item(entry, intl.SOURCES[2], self.now)
        self.assertIsNotNone(result)
        self.assertNotIn("<b>", result["content"])
        self.assertNotIn("leak()", result["content"])

    def test_duplicate_id_is_stable(self):
        row = self.entry()
        one = intl._item(row, intl.SOURCES[2], self.now)
        two = intl._item(row, intl.SOURCES[2], self.now)
        self.assertEqual(one["id"], two["id"])

    def test_google_feed_search_scopes_to_publisher_and_iran_iraq(self):
        url = intl.google_publisher_feed("reuters.com")
        self.assertIn("news.google.com/rss/search", url)
        self.assertIn("reuters.com", url)
        self.assertIn("Iran", url)
        self.assertIn("Iraq", url)

    def test_no_full_article_paywall_scraping(self):
        with patch.object(intl.requests, "get", return_value=Mock(status_code=503, content=b"")) as get:
            intl._fetch_source(intl.SOURCES[0], self.now, set())
        self.assertEqual(get.call_count, 2)
        urls = [call.args[0] for call in get.call_args_list]
        self.assertIn("rss.nytimes.com", urls[0])
        self.assertIn("news.google.com/rss/search", urls[1])
        self.assertTrue(all("/rss/" in url for url in urls))

    def test_official_feed_falls_back_to_verified_index_if_unavailable(self):
        spec = intl.SOURCES[0]
        row = self.entry(
            url="https://news.google.com/rss/articles/fallback",
            title="Iraq officials say security forces tightened border protections after new incidents",
            source={"href": "https://www.nytimes.com", "title": "The New York Times"},
        )
        with patch.object(intl.requests, "get", side_effect=[
            Mock(status_code=503, content=b""),
            Mock(status_code=200, content=b"<rss/>"),
        ]) as get, patch.object(intl.feedparser, "parse", return_value=SimpleNamespace(entries=[row])):
            result = intl._fetch_source(spec, self.now, set())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_name"], "The New York Times")
        self.assertEqual(get.call_count, 2)

    def test_bbc_english_uses_scoped_index_fallback(self):
        spec = intl.SOURCES[-1]
        self.assertIn("bbc.com/news", spec["fallback_feed"])


if __name__ == "__main__":
    unittest.main()
