import streamlit as st
import pandas as pd
import requests
import urllib.robotparser
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from playwright.sync_api import sync_playwright
import time
import logging
from functools import wraps

# --- Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Robots.txt parser and summary
rp = urllib.robotparser.RobotFileParser()
rp.set_url("https://www.bonappetit.com/robots.txt")
rp.read()

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException,))
)
def fetch_url(url):
    headers = {"User-Agent": "SmartCrawler/1.0"}
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp

# Summarize crawlability rules from robots.txt and allow download
def get_robots_summary():
    text = fetch_url(rp.url).text
    allowed, disallowed, sitemaps = [], [], []
    crawl_delay = None
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith('allow:'): allowed.append(line.split(':',1)[1].strip())
        elif line.lower().startswith('disallow:'): disallowed.append(line.split(':',1)[1].strip())
        elif line.lower().startswith('crawl-delay:'): crawl_delay = line.split(':',1)[1].strip()
        elif line.lower().startswith('sitemap:'): sitemaps.append(line.split(':',1)[1].strip())
    summary = (
        f"Allowed paths: {allowed}\n"
        f"Disallowed paths: {disallowed}\n"
        f"Crawl-delay: {crawl_delay}\n"
        f"Sitemap links: {sitemaps}\n"
    )
    return summary

# --- Streamlit Log Handler ---
class StreamlitLogHandler(logging.Handler):
    def __init__(self, placeholder):
        super().__init__()
        self.placeholder = placeholder
        self.log_content = ""
    def emit(self, record):
        msg = self.format(record)
        self.log_content += msg + "\n"
        self.placeholder.code(self.log_content, language="text")

# Placeholder for logs
log_placeholder = st.empty()
streamlit_handler = StreamlitLogHandler(log_placeholder)
streamlit_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(streamlit_handler)

# Caching decorator for sitemaps and API checks
def cache(func):
    cache_data = {}
    @wraps(func)
    def wrapper(*args, **kwargs):
        key = (func.__name__, args, tuple(sorted(kwargs.items())))
        if key not in cache_data:
            cache_data[key] = func(*args, **kwargs)
        return cache_data[key]
    return wrapper

@cache
def get_content_urls(start_year, start_month, start_week, end_year, end_month, end_week):
    sitemap_map, total_checked, total_crawlable = {}, 0, 0
    for year in range(start_year, end_year+1):
        m_start = start_month if year==start_year else 1
        m_end   = end_month   if year==end_year   else 12
        for month in range(m_start, m_end+1):
            for week in range(1,5):
                if year==end_year and month==end_month and week> end_week: break
                url = f"https://www.bonappetit.com/sitemap.xml?year={year}&month={month}&week={week}"
                try:
                    resp = fetch_url(url)
                    root = ET.fromstring(resp.content)
                    urls = [loc.text for loc in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
                except:
                    urls = []
                crawlable = [u for u in urls if rp.can_fetch("*", u)]
                sitemap_map[url] = crawlable
                total_checked += len(urls)
                total_crawlable += len(crawlable)
    return sitemap_map, total_checked, total_crawlable

# JS-heavy detection and data extraction
def extract_all_recipes(urls):
    recipes, js_heavy = [], []
    for u in urls:
        try:
            resp = fetch_url(u)
            soup = BeautifulSoup(resp.content, "lxml")
            if "/recipe/" in u:
                title = soup.select_one("h1[data-testid='ContentHeaderHed']").get_text(strip=True) if soup.select_one("h1[data-testid='ContentHeaderHed']") else "No title"
                desc  = soup.select_one("div.container--body-inner p").get_text(strip=True) if soup.select_one("div.container--body-inner p") else "No description"
                recipes.append({"title":title, "description":desc, "link":u})
                logger.info({"title":title, "description":desc, "link":u})
        except:
            js_heavy.append(u)
    return recipes, js_heavy

# Check for open APIs / RSS feeds
def check_open_apis():
    feeds = ["https://www.bonappetit.com/feed/rss", "https://www.bonappetit.com/api/"]
    available = []
    for f in feeds:
        try:
            fetch_url(f)
            available.append(f)
        except:
            pass
    return available

# --- Streamlit UI ---
st.title("🕷️ Web Crawler Dashboard For bonappetit website")

# Top-of-page summary and download
st.header("Summary of Crawlability Rules")
summary = get_robots_summary()
st.text(summary)
st.download_button(
    label="Download robots.txt summary",
    data=summary,
    file_name="robots_summary.txt",
    mime="text/plain"
)

# Sidebar inputs
st.sidebar.header("Sitemap Range")
start_year  = st.sidebar.number_input("Start Year", value=2024, step=1)
start_month = st.sidebar.number_input("Start Month", min_value=1, max_value=12, value=1)
start_week  = st.sidebar.number_input("Start Week", min_value=1, max_value=4, value=1)
end_year    = st.sidebar.number_input("End Year", value=2025, step=1)
end_month   = st.sidebar.number_input("End Month", min_value=1, max_value=12, value=4)
end_week    = st.sidebar.number_input("End Week", min_value=1, max_value=4, value=4)

if st.sidebar.button("Run Crawl"):
    logger.info("Crawl started.")
    try:
        # Fetch URLs
        sitemap_map, total_checked, total_crawlable = get_content_urls(
            start_year, start_month, start_week,
            end_year, end_month, end_week
        )
        # Crawlability score
        crawl_score = (total_crawlable/total_checked*100) if total_checked else 0
        st.metric("Crawlability Score", f"{crawl_score:.1f}%")

        # Extract data + JS detection
        all_urls = [u for urls in sitemap_map.values() for u in urls]
        recipes, js_heavy = extract_all_recipes(all_urls)

        # Display JS-heavy determination
        st.subheader("JavaScript-Heavy Check")
        if js_heavy:
            st.error(f"{len(js_heavy)} pages appear JS-heavy and need Playwright/Selenium")
        else:
            st.success("No JS-heavy pages detected (requests + BeautifulSoup sufficient)")

        # Extracted data
        st.subheader("Top Extracted Recipes")
        if recipes:
            st.dataframe(pd.DataFrame(recipes))
        else:
            st.write("No recipes extracted.")

        # Open APIs / RSS
        st.subheader("Open APIs / RSS Feeds")
        apis = check_open_apis()
        if apis:
            for link in apis:
                st.markdown(f"- [Preview]({link})")
        else:
            st.write("No open APIs or RSS feeds found.")

        # Recommendations
        st.subheader("Recommendations for Crawling Tools")
        if js_heavy:
            st.write("- Use **Playwright** or **Selenium** for JS-heavy pages.")
        st.write("- Use **Requests + BeautifulSoup** for static content.")
        st.write("- For large scale, **Scrapy** offers scheduling & pipelines.")

        # Visual sitemap
        st.subheader("Visual Sitemap")
        dot = "digraph sitemap {\n"
        for sm, urls in sitemap_map.items():
            dot += f'  "{sm}" [shape=box, color=lightblue];\n'
            for u in urls:
                dot += f'  "{sm}" -> "{u}";\n'
        dot += "}"
        st.graphviz_chart(dot)

        logger.info("Dashboard rendered successfully.")
    except Exception as e:
        logger.exception(f"Error during crawl: {e}")
    finally:
        logger.info("Crawl finished. Clearing logs.")
        log_placeholder.empty()
        logger.removeHandler(streamlit_handler)