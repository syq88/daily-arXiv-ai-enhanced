import os
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import scrapy


def text_of(selector, css):
    return " ".join(selector.css(css).xpath("string(.)").get(default="").split())


class ArxivSpider(scrapy.Spider):
    name = "arxiv"
    allowed_domains = ["arxiv.org"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_categories = {
            cat.strip() for cat in os.environ.get("CATEGORIES", "cs.CV").split(",")
            if cat.strip()
        }
        if not self.target_categories:
            raise ValueError("CATEGORIES must contain at least one arXiv category")
        self.start_urls = [
            f"https://arxiv.org/list/{cat}/new?skip=0&show=2000"
            for cat in sorted(self.target_categories)
        ]
        self.seen_ids = set()

    def parse(self, response):
        headings = response.css("h3").xpath("string(.)").getall()
        heading = next((h.strip() for h in headings if "Showing new listings for " in h), None)
        if heading is None:
            raise ValueError(f"Missing arXiv announcement date: {response.url}")
        listing_date = datetime.strptime(
            heading.split(" for ", 1)[1].strip(), "%A, %d %B %Y"
        ).date().isoformat()
        if getattr(self, "crawler", None) is not None:
            self.crawler.stats.inc_value("arxiv/listing_pages_parsed")
        self.logger.info("arXiv announcement batch: %s; URL: %s", listing_date, response.url)

        for paper in response.css("dl dt"):
            section = paper.xpath("preceding::h3[1]").xpath("string(.)").get(default="").strip().lower()
            if "replacement" in section:
                continue
            if not ("new submission" in section or "cross" in section):
                raise ValueError(f"Unrecognized arXiv listing section: {section}")
            link = paper.css("a[title='Abstract']::attr(href)").get()
            if not link or "/abs/" not in link:
                raise ValueError("Missing arXiv abstract link")
            paper_id = link.split("/abs/", 1)[1]
            identity = re.sub(r"v\d+$", "", paper_id)
            if identity in self.seen_ids:
                continue
            details = paper.xpath("following-sibling::dd[1]")
            subjects = text_of(details, ".list-subjects")
            categories = list(dict.fromkeys(re.findall(r"\(([\w.-]+)\)", subjects)))
            if not categories:
                raise ValueError(f"Missing categories for {paper_id}")
            if not self.target_categories.intersection(categories):
                continue

            title = re.sub(r"^Title:\s*", "", text_of(details, ".list-title"))
            authors = [
                " ".join(author.xpath("string(.)").get(default="").split())
                for author in details.css(".list-authors a")
            ]
            summary = text_of(details, "p.mathjax")
            if not title or not authors or not summary:
                raise ValueError(f"Incomplete listing metadata for {paper_id}")
            comment = re.sub(r"^Comments:\s*", "", text_of(details, ".list-comments")) or None
            self.seen_ids.add(identity)
            yield {
                "id": paper_id,
                "title": title,
                "authors": authors,
                "categories": categories,
                "summary": summary,
                "comment": comment,
                "abs": response.urljoin(link),
                "pdf": f"https://arxiv.org/pdf/{paper_id}",
                "listing_date": listing_date,
                "listing_type": "cross_list" if "cross" in section else "new",
            }

        # Follow the next listing page when a category exceeds the page size.
        current_skip = int(parse_qs(urlparse(response.url).query).get("skip", ["0"])[0])
        candidates = []
        for href in response.css("a[href*='skip=']::attr(href)").getall():
            url = response.urljoin(href)
            parsed = urlparse(url)
            if parsed.netloc != urlparse(response.url).netloc or parsed.path != urlparse(response.url).path:
                continue
            skip = int(parse_qs(parsed.query).get("skip", ["0"])[0])
            if skip > current_skip:
                candidates.append((skip, url))
        if candidates:
            yield response.follow(min(candidates)[1], callback=self.parse)
