#!/usr/bin/env python3
"""
scrape_monitor.py
------------------
Scrapes Uganda-focused sections of monitor.co.ug (National, Education, Business)
and writes the results to digest.json, which uganda-digest.html reads on load.

This is a SERVER-SIDE script — run it on your own machine or a scheduled job
(cron / Task Scheduler). It never runs in the browser, which is what lets it
get around the CORS restriction that blocks a client-side scraper.

USAGE
    pip install requests beautifulsoup4 lxml
    python scrape_monitor.py

    Optional flags:
    python scrape_monitor.py --no-fetch-articles   # faster, skips per-article
                                                    # word counts, uses estimates
    python scrape_monitor.py --limit 15            # cap stories per category

OUTPUT
    Writes ./digest.json next to this script.

NOTE ON SELECTORS
    Monitor runs on Nation Media Group's CMS, and like most news sites its
    markup can change without notice. The selectors below are best-effort,
    based on the URL pattern for article links (which is stable) rather than
    fragile class names. If a scrape ever comes back empty, the first thing
    to check is whether monitor.co.ug changed its HTML — open a category page
    in your browser, use "Inspect" on a headline, and adjust ARTICLE_CONTENT_
    SELECTORS below to match what you see.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE = "https://www.monitor.co.ug"

# Category pages to pull from. Keys become the "tag" shown in the app.
# World news is intentionally excluded — it's international, not Uganda news.
CATEGORY_PAGES = {
    "National": f"{BASE}/uganda/news/national",
    "Education": f"{BASE}/uganda/news/education",
    "Business": f"{BASE}/uganda/business",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 1.0  # seconds — be polite, don't hammer the site
WORDS_PER_MINUTE = 200

# Article URLs on Monitor follow this pattern: end in a dash + a numeric ID.
# e.g. /uganda/news/national/road-upgrades-give-locals-shot-in-arm-5580414
ARTICLE_URL_RE = re.compile(r"^/uganda/[a-z-]+/[a-z-]+/[a-z0-9-]+-\d{5,9}/?$")

# Candidate CSS selectors for the main body text on an article page, tried
# in order until one returns content. Adjust/add to this list if the site's
# markup differs from what's assumed here.
ARTICLE_CONTENT_SELECTORS = [
    "article",
    "div.article-body",
    "div[class*='article'] div[class*='body']",
    "div[class*='content'] p",
    "main",
]

# Text fragments that mark a story as a longer/paywalled "PRIME" feature.
PRIME_MARKERS = ("PRIME",)


# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------

def fetch(url, session):
    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        print(f"  [warn] failed to fetch {url}: {exc}", file=sys.stderr)
        return None


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def extract_article_links(html, category_tag):
    """Find candidate article links + their visible text on a category page."""
    soup = BeautifulSoup(html, "lxml")
    seen = set()
    results = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        path = href if href.startswith("/") else href.replace(BASE, "", 1)
        if not ARTICLE_URL_RE.match(path):
            continue
        full_url = BASE + path if path.startswith("/") else href
        if full_url in seen:
            continue

        text = clean_text(a.get_text(" ", strip=True))
        if not text or len(text) < 8:
            continue  # likely an icon/thumbnail link with no real headline

        seen.add(full_url)
        results.append({"url": full_url, "raw_text": text, "tag": category_tag})

    return results


def split_headline_and_time(raw_text, category_tag):
    """
    Category pages often flatten headline + teaser + category + timestamp
    into one block of text. Peel the relative timestamp off the end
    (e.g. "... National 4 hours ago", "... Yesterday", "... Aug 27") and
    treat what's left as headline/excerpt.
    """
    time_pattern = re.compile(
        r"(\b(?:\d+\s*(?:min|mins|hour|hours)\s*ago|Yesterday|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s?\d{0,2})\b)\s*$",
        re.IGNORECASE,
    )
    text = raw_text
    # Strip a leading repeat of the category name if present (e.g. "National ...")
    text = re.sub(rf"^{re.escape(category_tag)}\s+", "", text)

    match = time_pattern.search(text)
    stamp = match.group(1) if match else "Recently"
    headline_block = text[: match.start()].strip() if match else text

    is_prime = any(marker in headline_block for marker in PRIME_MARKERS)
    headline_block = headline_block.replace("PRIME", "").strip()

    # If there's a long teaser glued on, the actual headline is usually the
    # first clause before it runs long — split on the first sentence-like
    # boundary once the block gets past ~90 chars, using the rest as excerpt.
    title, excerpt = headline_block, ""
    if len(headline_block) > 90:
        # crude sentence split; keeps first chunk as title
        parts = re.split(r"(?<=[a-z])(?=[A-Z])", headline_block, maxsplit=1)
        if len(parts) == 2:
            title, excerpt = parts[0].strip(), parts[1].strip()

    return title, excerpt, stamp, is_prime


def estimate_reading_time(article_html):
    if not article_html:
        return None
    soup = BeautifulSoup(article_html, "lxml")

    for selector in ARTICLE_CONTENT_SELECTORS:
        container = soup.select_one(selector)
        if not container:
            continue
        paragraphs = container.find_all("p")
        words = sum(len(clean_text(p.get_text()).split()) for p in paragraphs)
        if words >= 50:  # sanity floor so we don't measure nav/footer junk
            return max(1, round(words / WORDS_PER_MINUTE))

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_digest(fetch_articles=True, limit_per_category=10):
    session = requests.Session()
    all_stories = []

    for tag, page_url in CATEGORY_PAGES.items():
        print(f"Fetching category: {tag} ({page_url})")
        html = fetch(page_url, session)
        if not html:
            continue

        links = extract_article_links(html, tag)[:limit_per_category]
        print(f"  found {len(links)} candidate articles")

        for item in links:
            title, excerpt, stamp, is_prime = split_headline_and_time(
                item["raw_text"], tag
            )
            if not title:
                continue

            minutes = None
            if fetch_articles:
                time.sleep(DELAY_BETWEEN_REQUESTS)
                article_html = fetch(item["url"], session)
                minutes = estimate_reading_time(article_html)

            if minutes is None:
                # Fallback heuristic when we skip fetching or can't parse body:
                # PRIME/long-form features run longer than standard news briefs.
                minutes = 6 if is_prime else 3

            display_tag = f"{tag} · Full read" if is_prime else tag

            all_stories.append(
                {
                    "tag": display_tag,
                    "time": stamp,
                    "minutes": minutes,
                    "title": title,
                    "excerpt": excerpt or "Read the full story on Monitor.",
                    "url": item["url"],
                }
            )

    # De-duplicate by URL, keep first occurrence, sort quickest-first
    seen_urls = set()
    deduped = []
    for s in all_stories:
        if s["url"] in seen_urls:
            continue
        seen_urls.add(s["url"])
        deduped.append(s)
    deduped.sort(key=lambda s: s["minutes"])

    return deduped


def main():
    parser = argparse.ArgumentParser(description="Scrape monitor.co.ug into digest.json")
    parser.add_argument(
        "--no-fetch-articles",
        action="store_true",
        help="Skip fetching each article for word counts; use quick estimates instead (faster).",
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Max stories to pull per category (default 10)."
    )
    parser.add_argument(
        "--out", default="digest.json", help="Output file path (default ./digest.json)."
    )
    args = parser.parse_args()

    stories = build_digest(
        fetch_articles=not args.no_fetch_articles, limit_per_category=args.limit
    )

    now = datetime.now(timezone.utc).astimezone()
    output = {
        "generated_at": now.isoformat(),
        "date_display": now.strftime("%A, %B %-d, %Y") if sys.platform != "win32"
        else now.strftime("%A, %B %#d, %Y"),
        "stories": stories,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {len(stories)} stories to {args.out}")
    if not stories:
        print(
            "No stories were found. Monitor's markup may have changed — "
            "see the NOTE ON SELECTORS comment at the top of this file.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
