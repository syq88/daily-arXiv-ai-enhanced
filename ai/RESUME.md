# AI enhancement recovery

- A failed paper is logged and skipped. Other completed papers are published.
  A batch with errors and no usable results still fails, so invalid credentials
  or a provider outage do not silently publish an empty report.
- Each completed summary is flushed to an atomic checkpoint immediately.
  A running job also writes a flushed `*_AI_enhanced_*.jsonl.partial` progress
  file. Final output replaces the published JSONL only after a successful batch.
- Run `python enhance.py --data ../data/YYYY-MM-DD.jsonl` again to resume.
  The default checkpoint folder is `.ai-checkpoints` beside the input file.
  Use `--checkpoint-dir PATH` to choose a persistent folder.
- Checkpoints are reused only when the input paper, model, API base URL,
  language, prompts and output schema match. Failed or invalid results are
  retried on the next run. No API key is stored in checkpoint records.
- The GitHub workflow restores checkpoints from Actions cache before AI
  processing and saves the updated cache even when that step fails.
  Each run attempt has a unique cache key so new progress can be saved.
  These checkpoints are outside the source checkout and are not published
  to the main or data branches.
- Actions cache is best-effort, not permanent storage: eviction or a forcibly
  terminated runner before the cache-save step can lose that run's new
  checkpoints. Re-running the job then processes any unavailable results again.
  Checkpointing is per completed paper and cannot resume inside one API call.
- The crawler still gathers the current paper list. Resuming AI processing
  reuses matching papers in that list; it does not automatically backfill old
  arXiv dates.
- The workflow restores the `data` branch before crawling and deduplication.
  Only successfully published AI outputs from earlier dates count as history,
  so a raw paper whose AI request failed can be retried if it is crawled again.
  History has no seven-day limit; IDs are compared without arXiv version suffixes.
  A rerun on the same Beijing date can still use its per-paper checkpoints.
  When every crawled paper already appeared on an earlier date, the workflow
  skips AI processing and does not create a new date on the website.
