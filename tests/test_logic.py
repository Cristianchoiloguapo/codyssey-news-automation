import unittest

from news_summary import Article, clean_url, keyword_match, parse_date


class NewsLogicTest(unittest.TestCase):
    def test_keyword_filter(self):
        words = ["AI", "OpenAI", "machine learning"]
        self.assertTrue(keyword_match("AI regulation announced", words))
        self.assertTrue(keyword_match("OpenAI releases a model", words))
        self.assertTrue(keyword_match("New MACHINE LEARNING advances", words))
        self.assertFalse(keyword_match("She said hello", words))
        self.assertFalse(keyword_match("gardening tips", words))

    def test_url_normalization(self):
        self.assertEqual(
            clean_url("https://Example.COM/news?id=1#section"),
            "https://example.com/news?id=1",
        )
        with self.assertRaises(ValueError):
            clean_url("file:///etc/passwd")

    def test_date_missing_not_invented(self):
        self.assertEqual(parse_date({}), "")


if __name__ == "__main__":
    unittest.main()
