class DailyArxivPipeline:
    """The listing already contains metadata; avoid a separate API call per paper."""

    def process_item(self, item, spider):
        required = ("id", "title", "authors", "categories", "summary", "abs", "pdf")
        missing = [field for field in required if not item.get(field)]
        if missing:
            raise ValueError(f"Incomplete paper {item.get('id')}: {', '.join(missing)}")
        return item
