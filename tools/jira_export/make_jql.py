"""Generate JQL chunks + Jira XML export URLs for the incident keys in the dataset.

Jira's XML export (Issue Navigator -> Export -> XML) includes full description,
comments, resolution, resolved date, components and labels — everything the
dataset JSON is missing. No API token needed; it uses the browser session.

Usage (from repo root):
    python tools/jira_export/make_jql.py [--chunk 200]
Output: docs/jira_export/jql/chunk_NN.jql, docs/jira_export/jql/urls.txt
"""
import argparse
import json
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "docs" / "kronik_sorunlar_son_1_yil_data.json"
OUT = ROOT / "docs" / "jira_export" / "jql"
BASE = "https://digiport.arcelik.com/sr/jira.issueviews:searchrequest-xml/temp/SearchRequest.xml"


def main():
    ap = argparse.ArgumentParser()
    # 200 keys keeps the export URL (~4 KB encoded) under Jira/Tomcat request-line limits.
    ap.add_argument("--chunk", type=int, default=200)
    args = ap.parse_args()

    data = json.loads(DATASET.read_text(encoding="utf-8"))
    keys = sorted({t["key"] for t in data["tickets"]}, key=lambda k: int(k.split("-")[1]))

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("chunk_*.jql"):
        old.unlink()

    urls = []
    for i in range(0, len(keys), args.chunk):
        n = i // args.chunk + 1
        jql = f"key in ({', '.join(keys[i:i + args.chunk])}) ORDER BY key ASC"
        (OUT / f"chunk_{n:02d}.jql").write_text(jql, encoding="utf-8")
        q = urllib.parse.urlencode({"jqlQuery": jql, "tempMax": args.chunk + 50})
        urls.append(f"# chunk_{n:02d}  ({keys[i]} .. {keys[min(i + args.chunk, len(keys)) - 1]})\n{BASE}?{q}\n")

    (OUT / "urls.txt").write_text("\n".join(urls), encoding="utf-8")
    print(f"{len(keys)} keys -> {len(urls)} chunks in {OUT}")


if __name__ == "__main__":
    main()
