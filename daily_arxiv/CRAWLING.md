# Crawl scope and announcement dates

- `CATEGORIES` is read from the environment (the GitHub workflow uses the
  repository variable). `cs.CV` alone selects papers indexed in Computer Vision,
  including papers whose primary category is elsewhere and secondary category
  is `cs.CV`. It does not select every paper from those other categories.
- The crawler reads the current `/list/<category>/new` announcement batch,
  including new submissions and cross-lists, excluding replacements. It follows
  listing pagination and emits each arXiv paper only once across categories.
- Titles, authors, abstracts, comments and all categories are extracted directly
  from the listing HTML. A separate API request per paper is unnecessary and
  formerly caused HTTP 429 responses and silently dropped items.
- Each paper records `listing_date` (the date shown by arXiv) and `listing_type`.
  The output filename uses the Beijing run date. These are distinct from the
  author's original submission timestamp.
- arXiv normally announces at 20:00 US Eastern, Sunday through Thursday.
  This corresponds to 08:00 Beijing during US daylight saving time, or 09:00
  during US standard time, Monday through Friday. Holiday exceptions apply.
  A 03:00 Beijing run normally sees the previous announcement batch. The
  requested 03:00 schedule is retained; running a job does not make arXiv
  release a new batch. Official schedule:
  https://info.arxiv.org/help/availability.html
- `python -m daily_arxiv.crawl --output ../data/YYYY-MM-DD.jsonl`, run from
  `daily_arxiv/`, validates the crawl's completion. Failed requests, parse errors,
  incomplete metadata or pipeline failures return a nonzero exit code so that
  Actions does not publish a partial crawl as a successful collection.
- Historical deduplication is separate: the workflow restores the `data` branch
  first and removes IDs successfully published on earlier dates before AI work.
  The `/new` crawler does not automatically backfill batches missed during a
  multi-day outage.
