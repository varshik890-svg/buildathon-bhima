---
name: scrape-and-sort
description: Pull tables or lists out of a web page or saved .html file, sort them numerically, save JSON/CSV
tags: [scrape, data, web, html]
---

# Scrape and sort

## From the harness (bhima.scrape)

```
python -m bhima.scrape <url-or-file.html> --sort <column> [--desc] [--csv out.csv] [--json out.json] [--limit N]
```

- Tables become one dict per row keyed by header cells; lists become `{"item": text, "list": n}`.
- With `--sort`, only the table that has that column is kept (pages carry navboxes and footers too).
- Sorting is numeric-aware: "1,200", "$15", "3.2k", "-0.18%" sort as numbers; blanks go last.
- Network fetches are capped at 2 MB, 15 s timeout, User-Agent `bhima-harness`. JS-rendered pages look empty: say so, do not guess.

## Inside a project that cannot import bhima (stdlib only)

Write a small `html.parser.HTMLParser` subclass:

1. On `<tr>` start a row; on `<td>`/`<th>` start a cell; collect `handle_data` text into the open cell; on `</td>`/`</th>` close it (`" ".join(text.split())`).
2. First row whose cells are all `<th>` is the header; lower-case the keys if the task asks for `sku`, `price`, ...
3. Convert types explicitly: `float(price.replace(",", ""))`, `int(stock)`.
4. Sort with `sorted(rows, key=lambda r: r[sort_by], reverse=descending)` and raise `KeyError`/`ValueError` for an unknown column.
5. Test against the saved HTML file with exact row counts and the first item after sorting.

Report the row count and the columns you found.
