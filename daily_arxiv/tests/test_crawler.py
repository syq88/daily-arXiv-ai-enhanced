import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scrapy.http import HtmlResponse, Request
from daily_arxiv.spiders.arxiv import ArxivSpider
from daily_arxiv.pipelines import DailyArxivPipeline
from daily_arxiv.crawl import crawl_succeeded


def entry(number, paper_id, primary="cs.CV", secondary=""):
    return f'''<dl><dt><a name="item{number}">[{number}]</a>
    <a title="Abstract" href="/abs/{paper_id}">arXiv:{paper_id}</a></dt>
    <dd><div class="list-title"><span>Title:</span> Test <em>world</em> model</div>
    <div class="list-authors"><a>Alice</a>, <a>Bob</a></div>
    <div class="list-subjects"><span class="primary-subject">Subject ({primary})</span>{secondary}</div>
    <div class="list-comments"><span>Comments:</span> Code: <a href="https://example.org">https://example.org</a></div>
    <p class="mathjax">An abstract with <em>inline</em> content.</p></dd></dl>'''


def response(body, url="https://arxiv.org/list/cs.CV/new?skip=0&show=2000"):
    html = '<h3>Showing new listings for Friday, 23 October 2026</h3>' + body
    return HtmlResponse(url=url, body=html.encode("utf-8"), encoding="utf-8", request=Request(url))


class ListingCrawlerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"CATEGORIES": "cs.CV"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.spider = ArxivSpider()

    def test_metadata_comes_from_listing_without_api_requests(self):
        records = list(self.spider.parse(response('<h3>New submissions</h3>' + entry(1, "2610.30814"))))
        self.assertEqual(1, len(records))
        paper = DailyArxivPipeline().process_item(records[0], self.spider)
        self.assertEqual("Test world model", paper["title"])
        self.assertEqual(["Alice", "Bob"], paper["authors"])
        self.assertEqual("An abstract with inline content.", paper["summary"])
        self.assertEqual("2026-10-23", paper["listing_date"])
        self.assertEqual("https://arxiv.org/abs/2610.30814", paper["abs"])
        self.assertIn("https://example.org", paper["comment"])

    def test_secondary_category_cross_lists_are_kept_and_replacements_skipped(self):
        body = '<h3>Cross submissions</h3>' + entry(1, "cross", "cs.RO", "; Vision (cs.CV)")
        body += '<h3>Replacement submissions</h3>' + entry(2, "oldv2")
        papers = list(self.spider.parse(response(body)))
        self.assertEqual(["cross"], [p["id"] for p in papers])
        self.assertEqual(["cs.RO", "cs.CV"], papers[0]["categories"])
        self.assertEqual("cross_list", papers[0]["listing_type"])

    def test_unrelated_category_is_skipped(self):
        papers = list(self.spider.parse(response('<h3>Cross submissions</h3>' + entry(1, "other", "cs.CL"))))
        self.assertEqual([], papers)

    def test_duplicates_across_categories_and_versions_are_emitted_once(self):
        first = response('<h3>New submissions</h3>' + entry(1, "2610.30814v1"))
        second = response('<h3>Cross submissions</h3>' + entry(2, "2610.30814v2"))
        self.assertEqual(1, len(list(self.spider.parse(first))))
        self.assertEqual([], list(self.spider.parse(second)))

    def test_missing_abstract_is_an_explicit_error(self):
        body = ('<h3>New submissions</h3>' + entry(1, "broken")).replace('class="mathjax"', 'class="missing"')
        with self.assertRaisesRegex(ValueError, "Incomplete listing metadata"):
            list(self.spider.parse(response(body)))

    def test_changed_page_layout_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "Missing arXiv announcement date"):
            list(self.spider.parse(HtmlResponse(url="https://arxiv.org/list/cs.CV/new", body=b"unavailable", encoding="utf-8")))

    def test_pagination_follows_next_offset_only(self):
        body = '<h3>New submissions</h3>' + entry(1, "one")
        body += '<a href="?skip=4000&amp;show=2000">Later</a><a href="?skip=2000&amp;show=2000">Next</a>'
        results = list(self.spider.parse(response(body)))
        self.assertEqual("https://arxiv.org/list/cs.CV/new?skip=2000&show=2000", results[-1].url)

    def test_pipeline_requires_complete_metadata(self):
        with self.assertRaisesRegex(ValueError, "Incomplete paper"):
            DailyArxivPipeline().process_item({"id": "missing"}, self.spider)

    def test_failed_requests_and_dropped_items_cannot_report_success(self):
        base = {"finish_reason": "finished", "arxiv/listing_pages_parsed": 1}
        for extra in [{"log_count/ERROR": 11}, {"retry/max_reached": 1}, {"spider_exceptions/ValueError": 1}, {"finish_reason": "shutdown"}, {"log_count/CRITICAL": 1}, {"httperror/response_ignored_count": 1}, {"arxiv/listing_pages_parsed": 0}, {"robotstxt/forbidden": 1}]:
            with self.subTest(extra=extra):
                self.assertFalse(crawl_succeeded({**base, **extra}))
        self.assertFalse(crawl_succeeded({}))
        self.assertTrue(crawl_succeeded({**base, "item_scraped_count": 0}))


if __name__ == "__main__":
    unittest.main()
