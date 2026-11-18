---
name: scrape-and-sort
description: Fetch a web page, pull out its tables or lists, sort them and save JSON/CSV with bhima.scrape
tags: [scrape, data, web]
---

# Scrape and sort with bhima.scrape

Command line:

```
python -m bhima.scrape <url> --sort <column> [--desc] [--csv out.csv] [--json out.json]
```

Python:

```python
from bhima import scrape
rows = scrape.scrape("https://example.com/table", sort_by="price")
scrape.to_csv(rows, "out.csv")
```

- Tables become one dict per row, keyed by the header cells. Lists become `{"item": text, "list": n}`.
- Sorting is numeric-aware: "1,200", "$15", "3.5%" sort as numbers; blanks go last.
- Pages are capped at 2 MB with a 15 s timeout and the User-Agent `bhima-harness`. JavaScript-rendered pages will look empty; say so instead of guessing.
- Check `robots.txt` and the site's terms; report the row count and the columns you found.
