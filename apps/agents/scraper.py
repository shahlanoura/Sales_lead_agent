"""Polite website fetcher: honours robots.txt, small page budget, no guessing."""
import re
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

UA = "WebfolksSalesBot/1.0"
HEADERS = {"User-Agent": UA}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
HINTS = ("contact", "about", "team", "company", "leadership", "people", "founder", "management", "our-story")


@dataclass
class Site:
    text: str = ""
    urls: list = field(default_factory=list)
    emails: dict = field(default_factory=dict)  # email -> source url


@lru_cache(maxsize=256)
def _robots(origin: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        r = requests.get(origin + "/robots.txt", headers=HEADERS, timeout=8)
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
            rp.modified()
        else:
            rp.allow_all = True
    except requests.RequestException:
        rp.allow_all = True
    return rp


def _get(url: str):
    p = urlparse(url)
    if not _robots(f"{p.scheme}://{p.netloc}").can_fetch(UA, url):
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            return r.text
    except requests.RequestException:
        pass
    return None


def fetch_site(website: str, domain: str, max_extra_pages: int = 3, max_chars: int = 12000) -> Site:
    site = Site()
    html = _get(website)
    if not html:
        return site
    pages = [(website, html)]
    soup = BeautifulSoup(html, "html.parser")
    extra = []
    for a in soup.find_all("a", href=True):
        href = urljoin(website, a["href"])
        if urlparse(href).netloc.lower().removeprefix("www.") == domain and any(h in href.lower() for h in HINTS):
            if href not in extra and href != website:
                extra.append(href)
    for url in extra[:max_extra_pages]:
        h = _get(url)
        if h:
            pages.append((url, h))

    chunks = []
    for url, h in pages:
        s = BeautifulSoup(h, "html.parser")
        for a in s.select("a[href^='mailto:']"):
            e = a["href"][7:].split("?")[0].strip().lower()
            site.emails.setdefault(e, url)
        for tag in s(["script", "style", "noscript"]):
            tag.decompose()
        txt = " ".join(s.get_text(" ").split())
        for e in EMAIL_RE.findall(txt):
            site.emails.setdefault(e.lower(), url)
        chunks.append(f"[{url}]\n{txt}")
        site.urls.append(url)
    site.emails = {e: u for e, u in site.emails.items()
                   if e.endswith("@" + domain) or e.endswith("." + domain)
                   if not e.endswith(BAD_EMAIL_SUFFIX)}
    site.text = "\n\n".join(chunks)[:max_chars]
    return site
