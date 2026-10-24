#!/usr/bin/env python3
"""Summarize numeric CSV columns: count, mean, median, min, max.

    python summarize.py data.csv [--out summary.json]
"""
import argparse
import csv
import json


def median(values):
    s = sorted(values)
    return s[len(s) // 2]


def summarize(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    out = {}
    for i, name in enumerate(header):
        vals = []
        for r in body:
            vals.append(float(r[i]))
        out[name] = {"count": len(vals), "mean": sum(vals) / len(vals), "median": median(vals),
                     "min": min(vals), "max": max(vals)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default="summary.json")
    a = ap.parse_args()
    s = summarize(a.csv)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    print(json.dumps(s, indent=2))


if __name__ == "__main__":
    main()
