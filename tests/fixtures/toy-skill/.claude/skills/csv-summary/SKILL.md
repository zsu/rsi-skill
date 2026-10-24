---
name: csv-summary
description: Summarize a CSV file of numbers - count, mean, median, min and max per numeric column - into summary.json and a short summary.md. Use when the user asks to summarize, profile or describe the numeric columns of a CSV file.
---

# csv-summary

Summarize the numeric columns of a CSV file.

## Steps

1. Find the CSV file the user means. If there are several and the user did not say which, summarize each.
2. Run `python <this skill>/scripts/summarize.py <file.csv> --out summary.json` from the working directory.
3. Write `summary.md` with one table: column, count, mean, median, min, max. Round to 2 decimals.
4. Reply with the table and one sentence on anything unusual (for example a column with very few values).

If nobody is available to answer questions, make a reasonable choice and say what you assumed.
