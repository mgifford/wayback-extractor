#!/usr/bin/env python3
"""
Wayback Static Mirror

Features
- Enumerate archived URLs broadly (domain match + scheme/host variants) up to an inclusive cutoff
- Pick the best usable snapshot (<= cutoff) avoiding redirects/stored 404s
- Download HTML and same-site assets; rewrite links and CSS url(...) to local relative paths
- Strip third-party JS (or all JS with a flag)
- Subtree scoping (--path-prefix)
- Robust retries and default rate limiter (polite to IA)
- Progress logs with rolling URLs/min
- Default output dir: domain_YYYYMMDD
- Reports: manifest.json, report.csv, report.md
- Debug flag to inspect CDX rows

Python 3.8+ recommended.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import threading
from collections import deque
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CDX = "https://web.archive.org/cdx/search/cdx"
CDX_ALTERNATE = "https://web.archive.org/cdx/search"
WAYBACK_AVAILABILITY = "https://archive.org/wayback/available"
WAYBACK_RAW = "https://web.archive.org/web"

UA = "WaybackStaticMirror/1.4 (+https://github.com/your-org/wayback-static-mirror)"
HTMLISH_PREFIXES = ("text/html", "application/xhtml+xml")
CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.IGNORECASE)


# ---------------- Rate limiter ----------------
class RateLimiter:
    def __init__(self, rps=0.5, burst=2):
        self.capacity = float(max(burst, 1))
        self.tokens = self.capacity
        self.fill = float(max(rps, 0.05))
        self.t0 = time.monotonic()
        self.lock = threading.Lock()

    def take(self, n=1.0):
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.t0
            self.t0 = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill)
            if self.tokens < n:
                sleep_time = (n - self.tokens) / self.fill
                if sleep_time > 0:
                    time.sleep(sleep_time)
                self.tokens = 0.0
                self.t0 = time.monotonic()
            else:
                self.tokens -= n


# ---------------- Utils ----------------
def to_ts_full(ts14: str) -> str:
    if not re.fullmatch(r"\d{14}", ts14):
        raise ValueError(f"Invalid IA timestamp: {ts14} (expected YYYYMMDDhhmmss)")
    return ts14


def to_ts_eod(date_str: str) -> str:
    # Accept YYYY-MM-DD or YYYYMMDD; return end-of-day ts YYYYMMDD235959
    if re.fullmatch(r"\d{8}", date_str):
        d = dt.datetime.strptime(date_str, "%Y%m%d")
    else:
        d = dt.datetime.strptime(date_str, "%Y-%m-%d")
    return d.strftime("%Y%m%d") + "235959"


def yyyymmdd(ts14: str) -> str:
    return ts14[:8]


def default_outdir(domain: str, cutoff_ts: str) -> str:
    return f"{domain}_{yyyymmdd(cutoff_ts)}"


def ensure_local_path(path: str) -> str:
    # Map URL path to local filesystem path, default index.html
    if not path or path.endswith("/"):
        path = (path or "/") + "index.html"
    path = path.split("?")[0].split("#")[0]
    return path.lstrip("/")


def is_same_site(url: str, root_host: str) -> bool:
    h = urlparse(url).netloc.lower()
    return h == root_host or h.endswith("." + root_host)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    retry = Retry(
        total=8,
        connect=8,
        read=8,
        backoff_factor=1.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# ---------------- CDX helpers ----------------
def _cdx(session: requests.Session, params: dict, timeout=90, endpoint=CDX):
    try:
        if "url" in params and "output" in params and params["output"] == "json":
            print(f"[DEBUG] CDX Query: {endpoint}?url={params['url']}")
            
        r = session.get(endpoint, params=params, timeout=timeout)
        r.raise_for_status()
        if not r.text.strip():
            return []
            
        try:
            rows = r.json()
            if not rows:
                return []
            header = rows[0]
            return [dict(zip(header, row)) for row in rows[1:]]
        except json.JSONDecodeError:
            print(f"[WARN] Failed to parse JSON response from CDX API")
            # Print first 100 chars of response for debugging
            print(f"Response starts with: {r.text[:100]}...")
            return []
    except Exception as e:
        print(f"[WARN] CDX query failed: {e}")
        return []


def _cdx_multi_endpoint(session: requests.Session, params: dict, timeout=90):
    """Try multiple CDX endpoints and combine results."""
    results = []
    
    # Try the prefix matchType explicitly (important for finding all URLs)
    if "matchType" not in params:
        params_with_prefix = {**params, "matchType": "prefix"}
        results.extend(_cdx(session, params_with_prefix, timeout, CDX))
    
    # Try primary endpoint
    results.extend(_cdx(session, params, timeout, CDX))
    
    # Try alternate endpoint if few results
    if len(results) < 10:
        results.extend(_cdx(session, params, timeout, CDX_ALTERNATE))
    
    return results


def check_availability_api(session: requests.Session, domain: str, cutoff_ts: str, debug=False):
    """Query the Wayback Machine's availability API for a domain."""
    try:
        # Query for the domain itself
        url = f"{WAYBACK_AVAILABILITY}?url={domain}&timestamp={cutoff_ts}"
        resp = session.get(url, timeout=30)
        results = []
        
        if resp.status_code == 200:
            data = resp.json()
            if 'archived_snapshots' in data and 'closest' in data['archived_snapshots']:
                snapshot = data['archived_snapshots']['closest']
                if snapshot.get('available', False) and snapshot.get('url'):
                    # Extract timestamp and original URL
                    timestamp = snapshot.get('timestamp', '')
                    original = snapshot.get('url', '')
                    if timestamp and original:
                        results.append({
                            'timestamp': timestamp,
                            'original': original,
                            'statuscode': '200',  # Assume 200 for available snapshots
                            'mimetype': 'text/html',  # Assume HTML
                            'digest': '',
                            'length': ''
                        })
            
        # Also try with www. prefix if not already present
        domain_with_www = domain if domain.startswith('www.') else f'www.{domain}'
        url = f"{WAYBACK_AVAILABILITY}?url={domain_with_www}&timestamp={cutoff_ts}"
        resp = session.get(url, timeout=30)
        
        if resp.status_code == 200:
            data = resp.json()
            if 'archived_snapshots' in data and 'closest' in data['archived_snapshots']:
                snapshot = data['archived_snapshots']['closest']
                if snapshot.get('available', False) and snapshot.get('url'):
                    # Extract timestamp and original URL
                    timestamp = snapshot.get('timestamp', '')
                    original = snapshot.get('url', '')
                    if timestamp and original:
                        results.append({
                            'timestamp': timestamp,
                            'original': original,
                            'statuscode': '200',  # Assume 200 for available snapshots
                            'mimetype': 'text/html',  # Assume HTML
                            'digest': '',
                            'length': ''
                        })
        
        if debug and results:
            print(f"[DEBUG] Availability API returned {len(results)} results")
            for r in results:
                print(f"  {r.get('timestamp')} {r.get('statuscode')} {r.get('original')}")
                
        return results
    except Exception as e:
        if debug:
            print(f"[WARN] Availability API query failed: {e}")
        return []


def cdx_query_variants(session: requests.Session, domain: str, cutoff_ts: str, subdomains=True, debug=False):
    """Enhanced CDX query with multiple strategies to maximize URL coverage.
    IMPORTANT: First get ALL URLs (no date filter), then filter by date later."""
    
    # Base params without date filter to get ALL archived URLs
    base_all_urls = {
        "output": "json",
        "fl": "timestamp,original,mimetype,statuscode,digest,length",
        "collapse": "urlkey",  # Only one snapshot per URL
        "filter": "statuscode:200",  # Only 200 responses
    }
    
    # Generate domain variants
    domain_variants = set()
    # Original form and lowercase
    domain_variants.add(domain)
    domain_variants.add(domain.lower())
    
    # With/without www
    if domain.startswith("www."):
        domain_variants.add(domain[4:])
    else:
        domain_variants.add("www." + domain)
        
    # Also add lowercase www version
    if not domain.lower().startswith("www."):
        domain_variants.add("www." + domain.lower())
    
    all_urls = []
    unique_originals = set()

    # STEP 1: First find ALL URLs ever archived for this domain (no date filter)
    if debug:
        print(f"[DEBUG] Searching for ALL archived URLs for {domain} variants: {domain_variants}")
    
    for d in domain_variants:
        # Method 1: Wildcard search (domain*)
        query_params = {**base_all_urls, "url": f"{d}*"}
        if debug:
            print(f"[DEBUG] Trying CDX query with: url={d}*")
            
        results = _cdx(session, query_params)
        
        # Track unique originals
        for r in results:
            url = r.get("original")
            if url and url not in unique_originals:
                unique_originals.add(url)
                
        all_urls.extend(results)
        
        # Method 2: Domain/* search with matchType
        if subdomains:
            query_params = {**base_all_urls, "url": f"{d}/*", "matchType": "domain"}
        else:
            query_params = {**base_all_urls, "url": f"{d}/*", "matchType": "host"}
            
        if debug:
            print(f"[DEBUG] Trying CDX query with: url={d}/*, matchType={'domain' if subdomains else 'host'}")
            
        results = _cdx(session, query_params)
        
        # Track unique originals
        for r in results:
            url = r.get("original")
            if url and url not in unique_originals:
                unique_originals.add(url)
                
        all_urls.extend(results)
    
    if debug:
        print(f"[DEBUG] Found {len(unique_originals)} unique URLs ever archived for this domain:")
        for i, url in enumerate(sorted(list(unique_originals))):
            if i < 25:  # Show first 25 only
                print(f"  {url}")
        if len(unique_originals) > 25:
            print(f"  ... and {len(unique_originals) - 25} more")
    
    # STEP 2: Filter by cutoff date
    filtered_by_date = [r for r in all_urls if r.get("timestamp", "") <= cutoff_ts]
    
    if debug:
        print(f"[DEBUG] After filtering by cutoff date {cutoff_ts}: {len(filtered_by_date)} snapshots")
    
    # STEP 3: Get the latest snapshot for each URL
    latest_per_url = {}
    for r in filtered_by_date:
        url = r.get("original", "")
        ts = r.get("timestamp", "")
        if not url or not ts:
            continue
        
        if url not in latest_per_url or ts > latest_per_url[url]["timestamp"]:
            latest_per_url[url] = r
    
    if debug:
        print(f"[DEBUG] Latest snapshot per URL (≤ cutoff): {len(latest_per_url)} URLs")
    
    # Convert to list
    uniq = list(latest_per_url.values())
    
    # Also try availability API as a fallback
    if len(uniq) < 5:
        avail_results = check_availability_api(session, domain, cutoff_ts, debug)
        
        # Add any new URLs from availability API
        for r in avail_results:
            url = r.get("original", "")
            if url and url not in latest_per_url:
                uniq.append(r)
    
    if debug:
        print(f"[DEBUG] Final unique URLs with snapshots: {len(uniq)}")
        for r in uniq[:25]:
            print(f"  {r.get('timestamp')} {r.get('statuscode')} {r.get('mimetype')}  {r.get('original')}")
    
    return uniq


def normalize_url(url: str, ignore_query_params=False):
    """Normalize a URL by optionally removing query parameters."""
    if not url:
        return url
        
    try:
        parsed = urlparse(url)
        if ignore_query_params:
            # Return URL without query parameters
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return url
    except Exception:
        return url


def latest_per_original(records, cutoff_ts: str, path_prefix: str = None, include_nonhtml=False, ignore_query_params=False):
    """Pick latest record <= cutoff for each 'original'.
    We've already filtered for 200 status codes, so focus on path filtering."""
    latest = {}
    for r in records:
        o = r.get("original", "")
        if not o:
            continue
        ts = r.get("timestamp", "")
        if not ts or ts > cutoff_ts:
            continue
        
        # Skip robots.txt regardless of include_nonhtml flag
        if urlparse(o).path.endswith("/robots.txt"):
            continue
            
        # Apply path prefix filter if specified
        if path_prefix:
            try:
                if not urlparse(o).path.startswith(path_prefix):
                    continue
            except Exception:
                continue
                
        # For HTML pages (main content), prefer text/html
        mime = r.get("mimetype", "").lower()
        if not include_nonhtml and not (mime.startswith("text/html") or "html" in mime):
            # Only include HTML files unless include_nonhtml is set
            continue
                
        # Use normalized URL as key if ignoring query parameters
        url_key = normalize_url(o, ignore_query_params) if ignore_query_params else o
        
        if (url_key not in latest) or ts > latest[url_key]["timestamp"]:
            latest[url_key] = r

    items = list(latest.values())
    return items


def cdx_history_for_url(session: requests.Session, url: str, cutoff_ts: str):
    base = {
        "url": url,
        "output": "json",
        "gzip": "false",
        "to": cutoff_ts,
        "fl": "timestamp,original,mimetype,statuscode,digest,length",
    }
    return _cdx(session, base)


def pick_best_snapshot(records, session, limiter, include_nonhtml=False, debug=False, timeout=30):
    """Find the best snapshot by trying each one, prioritizing newest that returns 200 and is valid.
    Iterate newest -> oldest; validate content/type after fetch."""
    
    # Sort newest to oldest
    sorted_records = sorted(records, key=lambda x: x["timestamp"], reverse=True)
    
    if debug and sorted_records:
        print(f"[DEBUG] Checking {len(sorted_records)} snapshots for best version")
    
    for r in sorted_records:
        try:
            resp = fetch_id(session, limiter, r["timestamp"], r["original"], timeout=timeout)
        except requests.exceptions.SSLError:
            resp = fetch_if(session, limiter, r["timestamp"], r["original"], timeout=timeout)
        
        if resp.status_code != 200:
            if debug:
                print(f"[DEBUG] Snapshot {r['timestamp']} returned status {resp.status_code}, skipping")
            continue
            
        if not origin_ok(resp):
            if debug:
                print(f"[DEBUG] Snapshot {r['timestamp']} original status not OK, skipping")
            continue
            
        if not include_nonhtml and not looks_html(resp):
            if debug:
                print(f"[DEBUG] Snapshot {r['timestamp']} is not HTML, skipping")
            continue
            
        # Found a good snapshot
        if debug:
            print(f"[DEBUG] Found good snapshot: {r['timestamp']} for {r['original']}")
        return r, resp.content
        
    # If we got here, no good snapshot found
    return None, None


# ---------------- Fetchers ----------------
def fetch_id(session: requests.Session, limiter: RateLimiter, ts: str, original: str, stream=False, timeout=30):
    limiter.take()
    try:
        return session.get(f"{WAYBACK_RAW}/{ts}id_/{original}", timeout=timeout, stream=stream)
    except requests.exceptions.Timeout:
        print(f"[ERROR] Request timed out for {original} after {timeout} seconds")
        resp = requests.Response()
        resp.status_code = 504  # Gateway Timeout
        return resp
    except Exception as e:
        print(f"[ERROR] Failed to fetch {original}: {str(e)}")
        resp = requests.Response()
        resp.status_code = 500
        return resp


def fetch_if(session: requests.Session, limiter: RateLimiter, ts: str, original: str, stream=False, timeout=30):
    limiter.take()
    try:
        return session.get(f"{WAYBACK_RAW}/{ts}if_/{original}", timeout=timeout, stream=stream)
    except requests.exceptions.Timeout:
        print(f"[ERROR] Request timed out for {original} after {timeout} seconds")
        resp = requests.Response()
        resp.status_code = 504  # Gateway Timeout
        return resp
    except Exception as e:
        print(f"[ERROR] Failed to fetch {original}: {str(e)}")
        resp = requests.Response()
        resp.status_code = 500
        return resp


def origin_ok(resp: requests.Response) -> bool:
    s = resp.headers.get("X-Archive-Orig-status") or resp.headers.get("X-Archive-Orig-Status")
    try:
        code = int(str(s).split()[0]) if s else None
    except Exception:
        code = None
    if code is not None:
        return 200 <= code < 300
    return 200 <= resp.status_code < 300


def looks_html(resp: requests.Response) -> bool:
    ctype = resp.headers.get("Content-Type", "").lower()
    return any(ctype.startswith(h) for h in HTMLISH_PREFIXES) or ("html" in ctype)


# ---------------- Rewriting ----------------
def rewrite_css_urls(css_bytes: bytes, base_url: str, root_host: str, out_css_dir: str) -> str:
    try:
        css = css_bytes.decode("utf-8")
    except UnicodeDecodeError:
        css = css_bytes.decode("latin-1", errors="replace")

    def repl(m):
        raw = m.group(2).strip()
        if raw.startswith(("data:", "#")):
            return m.group(0)
        absu = urljoin(base_url, raw)
        if is_same_site(absu, root_host):
            local = ensure_local_path(urlparse(absu).path)
            rel = os.path.relpath(local, out_css_dir)
            return f"url({rel})"
        return f"url({raw})"

    # Basic url(...) updates
    return CSS_URL_RE.sub(repl, css)


def rewrite_html_and_collect(html_bytes: bytes, base_url: str, root_host: str, banner_html=None, remove_all_scripts=False):
    try:
        html = html_bytes.decode("utf-8")
    except UnicodeDecodeError:
        html = html_bytes.decode("latin-1", errors="replace")

    soup = BeautifulSoup(html, "lxml")

    # Inject banner if provided
    if banner_html:
        body = soup.body
        if body:
            # Insert as first element in body
            body.insert(0, BeautifulSoup(banner_html, "lxml"))
        else:
            # Fallback: insert at top of html
            soup.insert(0, BeautifulSoup(banner_html, "lxml"))
    assets = set()

    # Remove Wayback toolbar if present
    wb = soup.find(id="wm-ipp")
    if wb:
        wb.decompose()

    # Scripts
    for s in list(soup.find_all("script")):
        src = s.get("src")
        if remove_all_scripts:
            s.decompose()
            continue
        if src:
            abs_src = urljoin(base_url, src)
            if not is_same_site(abs_src, root_host):
                s.decompose()
            else:
                assets.add(abs_src)

    # Attribute rewriting helper
    def rewrite_attr(tag, attr, collect=True):
        for el in list(soup.find_all(tag)):
            v = el.get(attr)
            if not v:
                continue
            absu = urljoin(base_url, v)
            if is_same_site(absu, root_host):
                local = ensure_local_path(urlparse(absu).path)
                current = ensure_local_path(urlparse(base_url).path)
                el[attr] = os.path.relpath(local, os.path.dirname(current) or ".")
                if collect:
                    assets.add(absu)

    # Links and assets
    rewrite_attr("a", "href", collect=False)
    rewrite_attr("img", "src", collect=True)

    for reltag in ("stylesheet", "icon", "shortcut icon", "apple-touch-icon"):
        for el in list(soup.find_all("link", rel=re.compile(reltag, re.I))):
            href = el.get("href")
            if not href:
                continue
            absu = urljoin(base_url, href)
            if is_same_site(absu, root_host):
                local = ensure_local_path(urlparse(absu).path)
                current = ensure_local_path(urlparse(base_url).path)
                el["href"] = os.path.relpath(local, os.path.dirname(current) or ".")
                assets.add(absu)

    # Inline style url(...)
    for style in soup.find_all(style=True):
        style_str = style["style"]

        def repl(m):
            inside = m.group(2).strip()
            absu = urljoin(base_url, inside)
            if is_same_site(absu, root_host):
                local = ensure_local_path(urlparse(absu).path)
                current = ensure_local_path(urlparse(base_url).path)
                rel = os.path.relpath(local, os.path.dirname(current) or ".")
                return f"url({rel})"
            return m.group(0)

        style["style"] = CSS_URL_RE.sub(repl, style_str)

    return str(soup), sorted(assets)


# ---------------- Assets ----------------
def download_asset(session: requests.Session, limiter: RateLimiter, ts: str, asset_url: str, outdir: str, timeout=30):
    p = urlparse(asset_url)
    local = ensure_local_path(p.path)
    out_path = os.path.join(outdir, local)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    resp = fetch_id(session, limiter, ts, asset_url, stream=True, timeout=timeout)
    if resp.status_code != 200:
        resp = fetch_if(session, limiter, ts, asset_url, stream=True, timeout=timeout)

    if resp.status_code == 200:
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(1 << 14):
                if chunk:
                    f.write(chunk)
        return local, True, out_path, resp.headers.get("Content-Type", "")
    return local, False, out_path, resp.headers.get("Content-Type", "")


# ---------------- Main ----------------
def main():

    ap = argparse.ArgumentParser(description="Build a static replica from the Wayback Machine.")
    ap.add_argument("domain", help="Root domain, e.g. digitalbuyingguide.org")
    ap.add_argument("--cutoff", default="2022-06-01",
                    help="Cutoff date (YYYY-MM-DD or YYYYMMDD). Interpreted as END of that day.")
    ap.add_argument("--cutoff-utc-ts", default=None,
                    help="Optional exact IA timestamp YYYYMMDDhhmmss to use instead of --cutoff.")
    ap.add_argument("--outdir", default=None,
                    help="Output directory. Default: domain_YYYYMMDD based on cutoff")
    ap.add_argument("--no-subdomains", action="store_true",
                    help="Do not include subdomains (default includes them).")
    ap.add_argument("--strip-all-js", action="store_true",
                    help="Remove all <script> tags, not just third-party.")
    ap.add_argument("--no-nonhtml", action="store_true",
                    help="Do NOT include non-HTML originals as pages (default: include them, e.g. PDFs).")
    ap.add_argument("--max", type=int, default=0,
                    help="Max pages to process (0 = no limit).")
    ap.add_argument("--path-prefix", default=None,
                    help="Only URLs whose path starts with this prefix, e.g. /en/")
    ap.add_argument("--rps", type=float, default=0.5,
                    help="Requests per second for IA. Default 0.5")
    ap.add_argument("--burst", type=int, default=2,
                    help="Burst size for rate limiter. Default 2")
    ap.add_argument("--quiet", action="store_true",
                    help="Minimal console output.")
    ap.add_argument("--log-assets", action="store_true",
                    help="Log each asset download.")
    ap.add_argument("--debug-cdx", action="store_true",
                    help="Print a sample of CDX rows and counts for troubleshooting.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print all CDX API requests and responses for troubleshooting.")
    ap.add_argument("--export-cdx", default=None,
                    help="Export all found CDX URLs and metadata to a CSV file before filtering.")
    ap.add_argument("--ignore-query-params", action="store_true",
                    help="Ignore URL query parameters when identifying unique URLs.")
    ap.add_argument("--timeout", type=int, default=30,
                    help="Timeout in seconds for HTTP requests (default: 30).")

    args = ap.parse_args()

    root_host = args.domain.lower()
    if args.cutoff_utc_ts:
        cutoff_ts = to_ts_full(args.cutoff_utc_ts)
    else:
        cutoff_ts = to_ts_eod(args.cutoff)
    outdir = args.outdir or default_outdir(root_host, cutoff_ts)
    os.makedirs(outdir, exist_ok=True)

    # By default, include non-HTML originals unless --no-nonhtml is specified
    include_nonhtml = not args.no_nonhtml

    limiter = RateLimiter(rps=args.rps, burst=args.burst)
    session = make_session()


    # Enumerate candidates broadly
    if not args.quiet:
        print(f"[INFO] Enumerating up to {cutoff_ts} for {root_host} (broad CDX and Availability API)…")

    try:
        # First check using original domain capitalization
        original_domain = args.domain  # Use original case from command line
        all_rows = cdx_query_variants(
            session, original_domain, cutoff_ts,
            subdomains=not args.no_subdomains,
            debug=args.debug_cdx or args.verbose
        )
        
        # Then add results with lowercase if different
        if original_domain.lower() != original_domain:
            lowercase_rows = cdx_query_variants(
                session, original_domain.lower(), cutoff_ts,
                subdomains=not args.no_subdomains,
                debug=args.debug_cdx or args.verbose
            )
            
            # Merge and deduplicate
            seen = set((r.get("original"), r.get("timestamp")) for r in all_rows)
            for r in lowercase_rows:
                key = (r.get("original"), r.get("timestamp"))
                if key not in seen:
                    all_rows.append(r)
                    seen.add(key)
        
        if args.verbose:
            print(f"[VERBOSE] CDX query for domain: {root_host}, cutoff: {cutoff_ts}, subdomains: {not args.no_subdomains}")
            print(f"[VERBOSE] CDX returned {len(all_rows)} rows")
            for r in all_rows[:10]:
                print(f"[VERBOSE] {r}")
                
        # Export all found CDX rows if requested
        if args.export_cdx:
            try:
                with open(args.export_cdx, "w", encoding="utf-8") as f:
                    # Write header
                    if all_rows:
                        f.write(",".join(all_rows[0].keys()) + "\n")
                        for r in all_rows:
                            f.write(",".join(str(r.get(k, "")) for k in all_rows[0].keys()) + "\n")
                print(f"[INFO] Exported {len(all_rows)} CDX rows to {args.export_cdx}")
            except Exception as e:
                print(f"[WARN] Failed to export CDX rows: {e}")
    except requests.exceptions.SSLError as e:
        print(f"[ERROR] TLS error talking to CDX: {e}")
        return 2
    except Exception as e:
        print(f"[ERROR] Failed to get CDX data: {e}")
        return 1

    # Pick latest per URL
    candidates = latest_per_original(
        all_rows, 
        cutoff_ts, 
        path_prefix=args.path_prefix, 
        include_nonhtml=include_nonhtml,
        ignore_query_params=args.ignore_query_params  # Use our new option
    )

    if not args.quiet:
        print(f"[INFO] Candidates: {len(candidates)} (after dedupe, ≤ cutoff)")
        print(f"[INFO] Output: {outdir}")

    if args.max:
        candidates = candidates[:args.max]

    start = time.monotonic()
    window = deque(maxlen=50)
    report_rows = []
    manifest = {"domain": root_host, "cutoff_ts": cutoff_ts, "pages": []}

    # Prepare banner HTML for injection
    banner_html = f'''<div style="background:#222;color:#fff;padding:8px 16px;font-size:1rem;text-align:center;z-index:9999;position:relative;box-shadow:0 2px 6px #0003;">
        Snapshot <b>{root_host}</b> from <a href="https://archive.org/web/" style="color:#ffd700;text-decoration:underline;">Archive.org</a> (Date: <b>{cutoff_ts[:4]}-{cutoff_ts[4:6]}-{cutoff_ts[6:8]}</b>)
    </div>'''

    processed = 0
    # Track the first CSS file we find and standardize its name
    standard_css_rel = "assets/stylesheets/application.css"
    standard_css_abs = os.path.join(outdir, standard_css_rel)
    css_copied = False
    css_seen = set()
    for idx, url_info in enumerate(candidates, 1):
        original = url_info["original"]

        if not args.quiet:
            now = time.monotonic()
            window.append(now)
            rate = (len(window) - 1) / ((window[-1] - window[0]) / 60) if len(window) > 1 else 0.0
            elapsed = now - start
            print(f"[PAGE {idx}/{len(candidates)}] {original} | elapsed {elapsed:0.1f}s | ~{rate:0.1f} urls/min")

        # Find the best snapshot directly
        chosen, html_bytes = pick_best_snapshot(
            [url_info],  # Try the candidate we already have
            session, 
            limiter, 
            include_nonhtml=include_nonhtml,
            debug=args.debug_cdx or args.verbose,
            timeout=args.timeout
        )
        
        # If no good snapshot from our candidate, try to find more from history
        if not chosen or not html_bytes:
            if not args.quiet or args.verbose:
                print(f"[INFO] No good snapshot for {original}, checking history...")
            
            # Get full history for this URL
            history = cdx_history_for_url(session, original, cutoff_ts)
            
            if args.verbose:
                print(f"[VERBOSE] Found {len(history)} historical snapshots for {original}")
            
            chosen, html_bytes = pick_best_snapshot(
                history,
                session,
                limiter,
                include_nonhtml=include_nonhtml,
                debug=args.debug_cdx or args.verbose,
                timeout=args.timeout
            )

        if not chosen or not html_bytes:
            report_rows.append({
                "original": original, "timestamp": "", "status": "failed",
                "reason": "no_good_snapshot", "assets": 0, "fallbacks": 0
            })
            if not args.quiet:
                print(f"[FAIL] {original} | no usable snapshot")
            continue

        # Write HTML after rewriting and collect assets
        p = urlparse(chosen["original"])
        local_html_rel = ensure_local_path(p.path)
        local_html_path = os.path.join(outdir, local_html_rel)
        os.makedirs(os.path.dirname(local_html_path), exist_ok=True)

        base_url = f"{p.scheme or 'http'}://{p.netloc}{p.path if p.path else '/'}"
        html_str, assets = rewrite_html_and_collect(
            html_bytes, base_url, root_host, banner_html=banner_html, remove_all_scripts=args.strip_all_js
        )
        with open(local_html_path, "wb") as f:
            f.write(html_str.encode("utf-8", errors="replace"))

        asset_results = []
        css_paths = []
        for a in assets:
            alocal, ok, out_path, ctype = download_asset(session, limiter, chosen["timestamp"], a, outdir, timeout=args.timeout)
            asset_results.append({"url": a, "local": alocal, "ok": ok, "content_type": ctype})
            if args.log_assets and not args.quiet:
                print(f"  [ASSET] {a} -> {alocal} {'OK' if ok else 'FAIL'}")
            if ok and (("text/css" in ctype.lower()) or alocal.lower().endswith(".css")):
                css_paths.append(out_path)
                # Copy the first CSS file we find to the standard name if not already done
                if not css_copied and os.path.exists(out_path):
                    os.makedirs(os.path.dirname(standard_css_abs), exist_ok=True)
                    try:
                        with open(out_path, "rb") as src, open(standard_css_abs, "wb") as dst:
                            dst.write(src.read())
                        css_copied = True
                        css_seen.add(alocal)
                    except Exception as e:
                        if not args.quiet:
                            print(f"  [WARN] Failed to copy CSS to standard name: {e}")

        # CSS rewriting inside downloaded files
        for css_path in css_paths:
            try:
                with open(css_path, "rb") as cf:
                    css_bytes = cf.read()
                out_css_dir = os.path.dirname(css_path)
                rewritten = rewrite_css_urls(css_bytes, base_url, root_host, out_css_dir)
                with open(css_path, "wb") as cf:
                    cf.write(rewritten.encode("utf-8", errors="replace"))
            except Exception as e:
                if not args.quiet:
                    print(f"  [WARN] CSS rewrite failed for {css_path}: {e}")

        # Rewrite all <link rel="stylesheet"> in the HTML to use the standard CSS name with correct relative path
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_str, "lxml")
        for link in soup.find_all("link", rel=lambda v: v and "stylesheet" in v):
            if link.get("href"):
                # Calculate proper relative path from this HTML file to the standard CSS
                html_dir = os.path.dirname(local_html_rel)
                rel_path = os.path.relpath(standard_css_rel, html_dir)
                # Normalize path separators to forward slashes for URLs
                rel_path = rel_path.replace(os.path.sep, '/')
                link["href"] = rel_path
        html_str = str(soup)

        # Overwrite the HTML file with the updated CSS reference
        with open(local_html_path, "wb") as f:
            f.write(html_str.encode("utf-8", errors="replace"))

        manifest["pages"].append({
            "original": chosen["original"],
            "timestamp": chosen["timestamp"],
            "local": local_html_rel,
            "assets": asset_results,
            "fallbacks": 0  # We no longer track fallbacks with the new approach
        })
        report_rows.append({
            "original": chosen["original"],
            "timestamp": chosen["timestamp"],
            "status": "ok",
            "reason": "",
            "assets": sum(1 for a in asset_results if a["ok"]),
            "fallbacks": 0  # We no longer track fallbacks with the new approach
        })
        processed += 1

        if not args.quiet:
            print(f"[OK] {chosen['original']} -> {local_html_rel} ({len(asset_results)} assets, fallbacks=0)")

        if args.max and processed >= args.max:
            break

        if not args.quiet and processed % 20 == 0:
            elapsed = time.monotonic() - start
            avg = processed / (elapsed / 60) if elapsed > 0 else 0.0
            print(f"[PROGRESS] {processed}/{len(candidates)} | {elapsed:0.1f}s | avg ~{avg:0.1f} urls/min")

    # Reports
    with open(os.path.join(outdir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    # Post-processing: Verify all CSS references are correct
    if not args.quiet:
        print(f"[INFO] Running post-processing to ensure CSS works on all pages...")
    
    # Make sure we have at least one CSS file
    if not css_copied and not os.path.exists(standard_css_abs):
        if not args.quiet:
            print(f"[WARN] No CSS file found during the mirroring process. Pages may be unstyled.")
    else:
        # Check all HTML files and ensure they have correct CSS references
        html_files = []
        for root, dirs, files in os.walk(outdir):
            for file in files:
                if file.endswith('.html'):
                    html_files.append(os.path.join(root, file))
                    
        fixed_css_refs = 0
        for html_file in html_files:
            try:
                with open(html_file, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                
                soup = BeautifulSoup(content, 'lxml')
                needs_saving = False
                
                # Check if there are stylesheet links
                css_links = soup.find_all("link", rel=lambda v: v and "stylesheet" in v)
                
                if not css_links:
                    # No stylesheet links, add one to the head
                    head = soup.head
                    if head:
                        # Calculate relative path from HTML file to standard CSS
                        rel_html_path = os.path.relpath(html_file, outdir)
                        html_dir = os.path.dirname(rel_html_path)
                        rel_path = os.path.relpath(standard_css_rel, html_dir)
                        # Normalize path separators for URLs
                        rel_path = rel_path.replace(os.path.sep, '/')
                        
                        new_link = soup.new_tag("link", href=rel_path, rel="stylesheet", type="text/css")
                        head.append(new_link)
                        needs_saving = True
                        fixed_css_refs += 1
                else:
                    # Verify all CSS links point to files that exist
                    for link in css_links:
                        href = link.get('href')
                        if href:
                            # Calculate absolute path to the referenced CSS
                            rel_html_path = os.path.relpath(html_file, outdir)
                            html_dir = os.path.dirname(rel_html_path)
                            css_path = os.path.normpath(os.path.join(html_dir, href))
                            css_abs_path = os.path.join(outdir, css_path)
                            
                            # If the CSS doesn't exist, point to our standard CSS
                            if not os.path.exists(css_abs_path):
                                # Calculate correct relative path to standard CSS
                                rel_path = os.path.relpath(standard_css_rel, html_dir)
                                rel_path = rel_path.replace(os.path.sep, '/')
                                link['href'] = rel_path
                                needs_saving = True
                                fixed_css_refs += 1
                
                # Save the file if changes were made
                if needs_saving:
                    with open(html_file, 'w', encoding='utf-8') as f:
                        f.write(str(soup))
            except Exception as e:
                if not args.quiet:
                    print(f"[WARN] Error fixing CSS in {html_file}: {e}")
        
        if not args.quiet and fixed_css_refs > 0:
            print(f"[INFO] Fixed CSS references in {fixed_css_refs} HTML files")

    with open(os.path.join(outdir, "report.csv"), "w", encoding="utf-8") as f:
        f.write("original,timestamp,status,reason,assets,fallbacks\n")
        for r in report_rows:
            f.write("{},{},{},{},{},{}\n".format(
                r["original"], r["timestamp"], r["status"], r["reason"], r["assets"], r["fallbacks"]
            ))

    md_path = os.path.join(outdir, "report.md")
    total = len(report_rows)
    okc = sum(1 for r in report_rows if r["status"] == "ok")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Wayback Static Mirror Report\n\n")
        f.write(f"- Domain: `{root_host}`\n")
        f.write(f"- Cutoff: `{cutoff_ts}`\n")
        f.write(f"- Pages processed: `{total}`\n")
        f.write(f"- OK: `{okc}`  Failed: `{total-okc}`\n\n")
        if total - okc:
            f.write("## Failures\n\n")
            for r in report_rows:
                if r["status"] != "ok":
                    f.write(f"- {r['original']}  reason: {r['reason']}  fallbacks: {r['fallbacks']}\n")
            f.write("\nSee `report.csv` and `manifest.json` for details.\n")

    if not args.quiet:
        elapsed = time.monotonic() - start
        avg = (processed / (elapsed / 60)) if elapsed > 0 else 0.0
        print(f"[DONE] Wrote {okc}/{total} pages to {outdir} | {elapsed:0.1f}s | avg ~{avg:0.1f} urls/min")
        print(f"[INFO] Reports: {outdir}/report.csv, {outdir}/report.md")

    return 0


if __name__ == "__main__":
    sys.exit(main())