"""Regression tests: never publish untranslated Persian or incomplete Sorani."""
import unittest
from unittest.mock import patch, Mock

import main
import sorani_quality


GOOD_TITLE = "ئێران ئامادەیە بۆ دانوستان لەگەڵ وڵاتانی ئەوروپا"
GOOD_BODY = (
    "ئێران ڕایگەیاند کە ئامادەیە بۆ دانوستان لەگەڵ وڵاتانی ئەوروپا "
    "دەربارەی پرۆگرامی ناوکی و هەموو پرسە پەیوەندیدارەکان. "
    "بەرپرسان هەروەها وتیان کە وردەکارییەکانی ئەم بابەتە دواتر ڕادەگەیەنرێت."
)
PERSIAN_BODY = (
    "مقامات امروز اعلام کردند که جلسه مهمی درباره وضعیت منطقه برگزار شده است "
    "و جزئیات بیشتری به زودی منتشر خواهد شد. روحانیون برای عبور از شرایط "
    "پیچیده کشور باید به کمک دولت بیایند."
)


class SoraniQualityTests(unittest.TestCase):
    def test_good_native_sorani_is_accepted(self):
        self.assertIsNone(sorani_quality.article_issue(GOOD_TITLE, GOOD_BODY))
        self.assertIsNotNone(main.validate_ai_news({"title": GOOD_TITLE, "body": GOOD_BODY}))

    def test_persian_grammar_in_title_is_blocked_even_if_body_is_sorani(self):
        title = "بهای طلا لە بازاڕەکانی هەولێر کەمبووەوە"
        self.assertIn("Persian", sorani_quality.article_issue(title, GOOD_BODY))

    def test_persian_body_is_blocked_even_when_sorani_headline_passes(self):
        body = "ئێران ڕایگەیاند کە " + PERSIAN_BODY + " هەواڵەکە دەبێت بە وردی بپشکنرێت."
        self.assertIn("Persian", sorani_quality.article_issue(GOOD_TITLE, body))

    def test_number_only_headline_is_not_news(self):
        self.assertIsNotNone(sorani_quality.article_issue("پزیشکان: ١", GOOD_BODY))
        self.assertIsNone(main.validate_ai_news({"title": "پزیشکان: ١", "body": GOOD_BODY}))

    def test_persian_copy_with_few_sorani_tokens_is_not_published(self):
        body = PERSIAN_BODY + " ئێران ڕایگەیاند کە ڕووداوەکە لەژێر چاودێریدایە."
        prepared = {"title": GOOD_TITLE, "body": body}
        self.assertIsNone(main.validate_ai_news(prepared, source_text=PERSIAN_BODY))

    def test_english_article_with_a_persian_phrase_is_rejected(self):
        body = GOOD_BODY + " " + PERSIAN_BODY
        self.assertIsNotNone(sorani_quality.article_issue(GOOD_TITLE, body))

    def test_untranslated_source_cannot_bypass_non_ai_fallback(self):
        item = {"title": GOOD_TITLE, "content": PERSIAN_BODY + " ئێران ڕایگەیاند کە ئەو هەواڵە نوێیە."}
        self.assertIsNone(main.format_without_ai(item))

    def test_sorani_non_ai_source_still_works(self):
        self.assertIsNotNone(main.format_without_ai({"title": GOOD_TITLE, "content": GOOD_BODY}))

    def test_safe_headline_derived_from_full_body(self):
        headline = main._fallback_headline(GOOD_BODY)
        self.assertGreaterEqual(len(sorani_quality._words(headline)), 3)
        self.assertFalse(headline.endswith(": ١"))

    def test_chunked_translation_keeps_entire_source(self):
        text = (PERSIAN_BODY + " ") * 18
        chunks = main._translation_chunks(text, max_chars=500)
        self.assertGreater(len(chunks), 2)
        self.assertEqual(" ".join(chunks), text.strip())

    def test_overlong_fallback_never_silently_cuts_source(self):
        with patch.object(main.requests, "post") as post:
            self.assertIsNone(main.translate_without_gemini({"title": "", "content": PERSIAN_BODY * 28}))
            post.assert_not_called()

    def test_translated_full_body_and_title_are_both_validated(self):
        title = "ایران آماده گفتگو درباره مسائل هسته ای است"
        body_translation = GOOD_BODY
        title_translation = GOOD_TITLE
        def response(value):
            return Mock(ok=True, json=lambda: [[[value, "src", None, None, 1]], None, "fa"])
        with patch.object(main.requests, "post", side_effect=[response(body_translation), response(title_translation)]) as post:
            result = main.translate_without_gemini({"title": title, "content": PERSIAN_BODY})
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], GOOD_TITLE)
        self.assertEqual(result["body"], GOOD_BODY)
        self.assertEqual(post.call_count, 2)

    def test_bad_machine_translation_is_not_accepted(self):
        def response(value):
            return Mock(ok=True, json=lambda: [[[value, "src", None, None, 1]], None, "fa"])
        with patch.object(main.requests, "post", return_value=response(PERSIAN_BODY)):
            self.assertIsNone(main.translate_without_gemini({"title": "", "content": PERSIAN_BODY}))

    def test_latin_source_not_mistaken_for_sorani(self):
        english = ("Iran says it is ready to negotiate with all nations and discuss regional developments. " * 2)
        self.assertIsNotNone(sorani_quality.article_issue(GOOD_TITLE, english))


if __name__ == "__main__":
    unittest.main()
