# Crawl scope and announcement dates

- `CATEGORIES` is read from the environment (the GitHub workflow uses the
  repository variable). It selects papers whose primary category matches a
  configured category. When `cs.CV` is configured, cross-listed papers are
  additionally included only when their categories contain both `cs.RO` and
  `cs.CV`. Thus CV primary papers and RO/CV overlap are kept, while cross-lists
  such as `cs.LG,cs.CV` or `cs.CL,cs.CV` are excluded unless their primary
  category was explicitly configured. Other category tags remain intact.
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
  The daily workflow is scheduled for 10:17 Beijing, with fallback triggers at
  11:47 and 14:17 (UTC 02:17, 03:47 and 06:17). Scheduled runs first check for
  an earlier successful run on the same Beijing date and skip processing if
  one exists. Manual dispatch always runs. The status check uses GitHub Actions
  read permission and allows processing if the API lookup is unavailable.
  GitHub may delay or drop scheduled events; the fallback times reduce this
  risk but cannot guarantee punctual execution. Weekend/holiday runs may still
  see the previous batch. Official arXiv schedule:
  https://info.arxiv.org/help/availability.html
- `python -m daily_arxiv.crawl --output ../data/YYYY-MM-DD.jsonl`, run from
  `daily_arxiv/`, validates the crawl's completion. Failed requests, parse errors,
  incomplete metadata or pipeline failures return a nonzero exit code so that
  Actions does not publish a partial crawl as a successful collection.
- Historical deduplication is separate: the workflow restores the `data` branch
  first and removes IDs successfully published on earlier dates before AI work.
  The `/new` crawler does not automatically backfill batches missed during a
  multi-day outage.


## Application-domain filter

After the listing crawl and before deduplication/AI summarization, the workflow
runs `python -m daily_arxiv.topic_filter`. It applies the editable rules in
`daily_arxiv/paper_filter_rules.json` to titles and the beginning of abstracts.
Explicit remote-sensing, biomedical, AI4Science, agriculture/ecology, industrial
inspection and other specialist applications are excluded. General CV and
spatial/embodied methods are retained; `physics`, `space`, `3D` and references
to evaluation domains alone are not exclusion criteria. Ambiguous cases are
kept. This is conservative rule-based filtering, not a semantic relevance model.

`PAPER_FILTER_ENABLED=false` in repository Variables bypasses the filter. The
filter makes no API requests and leaves kept paper records unchanged, so their
existing AI checkpoints remain reusable. If nothing remains, the existing
no-data gate skips AI work and publication successfully.

The filter writes a report with reasons, matched fields, rule/input hashes and
full excluded records before atomically replacing the daily input. A report
write failure leaves the original input intact. Reports are copied to the data
branch with a successful daily publication; the current report is also uploaded
as a 30-day Actions artifact even when no papers remain. Historical reports are
retained when switching branches. Run filter tests with:

```
python -m unittest discover -s daily_arxiv/tests -p test_topic_filter.py -v
```
