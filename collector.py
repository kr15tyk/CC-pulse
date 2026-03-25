"""
collector.py — Pulls all data from NIAP APIs, CC Portal, and CCTL labs.
Returns a single dict representing today's full snapshot.
"""

import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import config

HEADERS = {
      "User-Agent": (
                "CCPulse/1.0 (automated monitoring tool; "
                "contact your-email@example.com)"
      )
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ── Helpers ──────────────────────────────────────────────────────────────────

def get_json(url, params=None):
      try:
                r = SESSION.get(url, params=params, timeout=30)
                r.raise_for_status()
                return r.json()
except Exception as e:
        print(f"  [WARN] JSON fetch failed for {url}: {e}")
        return None

def get_html(url):
      try:
                r = SESSION.get(url, timeout=30)
                r.raise_for_status()
                return BeautifulSoup(r.text, "lxml")
except Exception as e:
        print(f"  [WARN] HTML fetch failed for {url}: {e}")
        return None

def get_rss(url):
      try:
                feed = feedparser.parse(url)
                return [
                    {
                        "title":    e.get("title", ""),
                        "link":     e.get("link", ""),
                        "summary":  e.get("summary", ""),
                        "published": e.get("published", ""),
                        "id":       e.get("id", e.get("link", "")),
                    }
                    for e in feed.entries
                ]
except Exception as e:
        print(f"  [WARN] RSS fetch failed for {url}: {e}")
        return []

# ── NIAP ─────────────────────────────────────────────────────────────────────

def collect_niap():
      print("[NIAP] Collecting...")
      base = config.NIAP_BASE
      eps  = config.NIAP_ENDPOINTS
      data = {}

    # PCL
      print("  PCL...")
      pcl = get_json(base + eps["pcl"])
      data["pcl"] = pcl or []

    # Protection Profiles
      print("  Protection Profiles...")
      pps = get_json(base + eps["pps"])
      data["pps"] = pps or []

    # Technical Decisions
      print("  Technical Decisions...")
      tds = get_json(base + eps["tds"])
      data["tds"] = tds or []

    # CCTL Directory
      print("  CCTL Directory...")
      cctls_raw = get_json(base + eps["cctls"])
      data["cctls"] = (
          cctls_raw.get("results", {}).get("cctls", [])
          if cctls_raw else []
      )

    # Events
      print("  Events...")
      ev_curr = get_json(base + eps["events_curr"])
      ev_prev = get_json(base + eps["events_prev"])
      curr = ev_curr.get("results", []) if ev_curr else []
      prev = ev_prev.get("results", []) if ev_prev else []
      data["events"] = curr + prev

    # News / Announcements
      print("  News & Announcements...")
      news_raw = get_json(base + eps["news"])
      data["news"] = news_raw.get("results", []) if news_raw else []

    print(f"  [NIAP] PCL:{len(data['pcl'])} PPs:{len(data['pps'])} "
                    f"TDs:{len(data['tds'])} Events:{len(data['events'])} "
                    f"News:{len(data['news'])}")
    return data

# ── CC PORTAL ────────────────────────────────────────────────────────────────

def parsecc_news(soup):
      items = []
      if not soup:
                return items
            content = soup.find("div", {"id": "main"}) or soup.find("div", class_="main") or soup
    for tag in content.find_all(["p", "li"]):
              text = tag.get_text(strip=True)
              link = tag.find("a")
              href = link["href"] if link and link.get("href") else ""
              if len(text) > 20:
                            items.append({"text": text, "link": href})
                    return items

def parsecc_pps(soup):
      rows = []
    if not soup:
              return rows
    table = soup.find("table")
    if not table:
              return rows
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    for tr in table.find_all("tr")[1:]:
              cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
                      row = dict(zip(headers, cells))
                      link = tr.find("a")
                      row["_link"] = link["href"] if link and link.get("href") else ""
                      rows.append(row)
              return rows

def parsecc_products(soup):
      rows = []
    if not soup:
              return rows
    table = soup.find("table")
    if not table:
              return rows
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    for tr in table.find_all("tr")[1:]:
              cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
                      row = dict(zip(headers, cells))
                      rows.append(row)
              return rows

def parsecc_communities(soup):
      items = []
    if not soup:
              return items
    content = soup.find("div", {"id": "main"}) or soup
    for a in content.find_all("a"):
              text = a.get_text(strip=True)
        href = a.get("href", "")
        if text:
                      items.append({"name": text, "link": href})
              return items

def collect_cc_portal():
      print("[CC Portal] Collecting...")
    base  = config.CC_PORTAL_BASE
    pages = config.CC_PORTAL_PAGES
    data  = {}

    print("  News...")
    data["news"] = parsecc_news(get_html(base + pages["news"]))
    print("  Protection Profiles...")
    data["pps"] = parsecc_pps(get_html(base + pages["pps"]))
    print("  Certified Products...")
    data["products"] = parsecc_products(get_html(base + pages["products"]))
    print("  Technical Communities...")
    data["communities"] = parsecc_communities(get_html(base + pages["communities"]))
    print("  Publications...")
    data["publications"] = parsecc_news(get_html(base + pages["publications"]))
    print("  PP RSS Feed...")
    data["pp_rss"] = get_rss(config.CC_PORTAL_RSS)

    print(f"  [CC Portal] News:{len(data['news'])} "
                    f"PPs:{len(data['pps'])} Products:{len(data['products'])}")
    return data

# ── CCTL Labs ────────────────────────────────────────────────────────────────

def scrapelab_items(url):
      """Generic scraper — extracts headlines/links from a lab's news/blog page."""
    soup = get_html(url)
    if not soup:
              return []
    items = []
    for tag in soup.find_all(["h2", "h3", "h4", "article"]):
              a = tag.find("a") if tag.name != "a" else tag
        if a and a.get_text(strip=True):
                      items.append({
                                        "title":    a.get_text(strip=True),
                                        "link":     a.get("href", ""),
                                        "published": "",
                                        "id":       a.get("href", a.get_text(strip=True)),
                      })
              return items[:20]  # cap at 20 most recent

def collect_cctl_labs():
      print("[CCTL Labs] Collecting...")
    results = {}
    for lab in config.CCTL_LABS:
              name = lab["name"]
        print(f"  {name}...")
        if lab["rss"]:
                      items = get_rss(lab["rss"])
elif lab["scrape"] and lab["url"]:
            items = scrapelab_items(lab["url"])
else:
            items = []
        results[name] = items
        print(f"    -> {len(items)} items")
    return results

# ── Master Snapshot ───────────────────────────────────────────────────────────

def collect_all():
      """Collect everything and return as a single timestamped snapshot dict."""
    snapshot = {
              "collected_at": datetime.now(timezone.utc).isoformat(),
              "niap":         collect_niap(),
              "cc_portal":    collect_cc_portal(),
              "cctl_labs":    collect_cctl_labs(),
    }
    return snapshot
