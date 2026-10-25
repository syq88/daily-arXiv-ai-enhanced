"""Run Scrapy and report incomplete crawls as failures, including pipeline errors."""
import argparse
import sys
from pathlib import Path

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from daily_arxiv.spiders.arxiv import ArxivSpider


def crawl_succeeded(stats):
    return (
        stats.get("finish_reason") == "finished"
        and stats.get("arxiv/listing_pages_parsed", 0) > 0
        and not stats.get("log_count/ERROR", 0)
        and not stats.get("log_count/CRITICAL", 0)
        and not stats.get("retry/max_reached", 0)
        and not stats.get("httperror/response_ignored_count", 0)
        and not stats.get("robotstxt/forbidden", 0)
        and not any(value for key, value in stats.items() if key.startswith("spider_exceptions/"))
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    settings = get_project_settings()
    settings.set("FEEDS", {output.as_uri(): {"format": "jsonlines", "encoding": "utf-8", "overwrite": True}}, priority="cmdline")
    process = CrawlerProcess(settings)
    crawler = process.create_crawler(ArxivSpider)
    process.crawl(crawler)
    process.start()
    stats = crawler.stats.get_stats()
    if not crawl_succeeded(stats):
        print("Crawl incomplete: refusing to publish partial results. Check the crawl errors above.", file=sys.stderr)
        return 1
    print(f"Crawl complete: {stats.get('item_scraped_count', 0)} papers saved to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
