# Uganda in Five — setup

Three files work together:

- `scrape_monitor.py` — server-side scraper. Pulls headlines from monitor.co.ug and writes `digest.json`.
- `digest.json` — the data file the page reads. Starts with a snapshot from Sept 2, 2026 so the page works immediately; overwritten each time you run the scraper.
- `uganda-digest.html` — the reader app. Loads `digest.json` on open.

## 1. Install dependencies

```
pip install requests beautifulsoup4 lxml
```

## 2. Run the scraper

```
python scrape_monitor.py
```

This fetches National, Education, and Business category pages, visits each article to estimate reading time from actual word count, and writes `digest.json` in the same folder. Takes roughly 30–60 seconds since it's polite about not hammering the site (1s delay between requests).

For a quicker but rougher run that skips visiting each article:

```
python scrape_monitor.py --no-fetch-articles
```

## 3. Serve the page

Browsers block a local HTML file from `fetch()`-ing a local JSON file directly (a stricter version of the same cross-origin issue from before). So don't just double-click `uganda-digest.html` — serve the folder instead:

```
cd path/to/this/folder
python -m http.server 8000
```

Then open `http://localhost:8000/uganda-digest.html`. The page will fetch `digest.json` correctly this way.

## 4. Keep it fresh automatically (optional)

Run the scraper on a schedule so `digest.json` updates every day without you thinking about it.

**Mac/Linux (cron):** run `crontab -e` and add a line like:

```
0 7 * * * cd /path/to/this/folder && /usr/bin/python3 scrape_monitor.py >> scrape.log 2>&1
```

This runs it daily at 7am. Adjust the path and time as needed.

**Windows (Task Scheduler):** create a basic task that runs daily, with:
- Program: `python`
- Arguments: `scrape_monitor.py`
- Start in: the folder containing the script

If you're keeping the local server running (step 3) as a background process too, the page will simply show the newer data next time you refresh it in the browser — no restart needed.

## If a scrape comes back empty

News sites change their HTML from time to time. Open `scrape_monitor.py` and read the "NOTE ON SELECTORS" comment near the top — it explains what to check in your browser's dev tools and which constants to adjust.
