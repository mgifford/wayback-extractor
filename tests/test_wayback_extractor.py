"""Comprehensive unit tests for wayback_extractor.py.

Covers pure utility helpers, the RateLimiter, HTTP-response helpers,
URL-deduplication logic, HTML/CSS rewriting, and the mocked HTTP layer.
All network calls are replaced with unittest.mock so the test suite
runs entirely offline.
"""

import json
import threading
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

import requests

import wayback_extractor as we


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(
    status_code: int = 200,
    content_type: str = "text/html",
    text: str = "",
    headers: dict | None = None,
) -> requests.Response:
    """Build a minimal :class:`requests.Response` for use in tests.

    Args:
        status_code: HTTP status code for the fake response.
        content_type: Value for the Content-Type header.
        text: Body text (will be encoded to UTF-8 bytes).
        headers: Additional headers to merge in.

    Returns:
        A :class:`requests.Response` populated with the given values.
    """
    resp = requests.Response()
    resp.status_code = status_code
    h = {"Content-Type": content_type}
    if headers:
        h.update(headers)
    resp.headers = requests.structures.CaseInsensitiveDict(h)
    resp._content = text.encode("utf-8")
    return resp


# ---------------------------------------------------------------------------
# Timestamp utilities
# ---------------------------------------------------------------------------

class TestToTsFull(unittest.TestCase):
    """Tests for :func:`wayback_extractor.to_ts_full`."""

    def test_valid_14_digit_string(self) -> None:
        """A 14-digit numeric string should be returned unchanged."""
        self.assertEqual(we.to_ts_full("20230101120000"), "20230101120000")

    def test_raises_on_too_short(self) -> None:
        """Strings shorter than 14 digits must raise ValueError."""
        with self.assertRaises(ValueError):
            we.to_ts_full("2023010112000")

    def test_raises_on_too_long(self) -> None:
        """Strings longer than 14 digits must raise ValueError."""
        with self.assertRaises(ValueError):
            we.to_ts_full("202301011200001")

    def test_raises_on_non_digits(self) -> None:
        """Non-digit characters must raise ValueError."""
        with self.assertRaises(ValueError):
            we.to_ts_full("2023010112000X")

    def test_raises_on_empty_string(self) -> None:
        """Empty string must raise ValueError."""
        with self.assertRaises(ValueError):
            we.to_ts_full("")


class TestToTsEod(unittest.TestCase):
    """Tests for :func:`wayback_extractor.to_ts_eod`."""

    def test_hyphenated_format(self) -> None:
        """YYYY-MM-DD should produce YYYYMMDD235959."""
        self.assertEqual(we.to_ts_eod("2023-01-15"), "20230115235959")

    def test_compact_format(self) -> None:
        """YYYYMMDD should produce YYYYMMDD235959."""
        self.assertEqual(we.to_ts_eod("20230115"), "20230115235959")

    def test_end_of_year(self) -> None:
        """Dec 31 should produce correct end-of-day timestamp."""
        self.assertEqual(we.to_ts_eod("2022-12-31"), "20221231235959")

    def test_invalid_format_raises(self) -> None:
        """An unrecognized date format should raise ValueError."""
        with self.assertRaises(ValueError):
            we.to_ts_eod("01/15/2023")


class TestYyyymmdd(unittest.TestCase):
    """Tests for :func:`wayback_extractor.yyyymmdd`."""

    def test_extracts_first_eight_chars(self) -> None:
        """Should return the first 8 characters of the timestamp."""
        self.assertEqual(we.yyyymmdd("20230115120000"), "20230115")

    def test_works_on_minimal_input(self) -> None:
        """Should work with any string of at least 8 characters."""
        self.assertEqual(we.yyyymmdd("20990101000000"), "20990101")


# ---------------------------------------------------------------------------
# Output-directory helper
# ---------------------------------------------------------------------------

class TestDefaultOutdir(unittest.TestCase):
    """Tests for :func:`wayback_extractor.default_outdir`."""

    def test_combines_domain_and_date(self) -> None:
        """Should produce domain_YYYYMMDD."""
        self.assertEqual(
            we.default_outdir("example.com", "20230115235959"),
            "example.com_20230115",
        )

    def test_dot_in_domain_preserved(self) -> None:
        """Dots in the domain name should be preserved as-is."""
        self.assertEqual(
            we.default_outdir("sub.example.com", "20231231000000"),
            "sub.example.com_20231231",
        )


# ---------------------------------------------------------------------------
# ensure_local_path
# ---------------------------------------------------------------------------

class TestEnsureLocalPath(unittest.TestCase):
    """Tests for :func:`wayback_extractor.ensure_local_path`."""

    def test_empty_path_becomes_index(self) -> None:
        """Empty path should become index.html."""
        self.assertEqual(we.ensure_local_path(""), "index.html")

    def test_root_slash_becomes_index(self) -> None:
        """Root '/' path should become index.html."""
        self.assertEqual(we.ensure_local_path("/"), "index.html")

    def test_trailing_slash_appends_index(self) -> None:
        """Trailing slash on a directory should append index.html."""
        self.assertEqual(we.ensure_local_path("/about/"), "about/index.html")

    def test_file_path_strips_leading_slash(self) -> None:
        """A file path should have its leading slash stripped."""
        self.assertEqual(we.ensure_local_path("/page.html"), "page.html")

    def test_query_string_stripped(self) -> None:
        """Query parameters should be removed."""
        self.assertEqual(we.ensure_local_path("/search?q=foo"), "search")

    def test_fragment_stripped(self) -> None:
        """Fragment identifiers should be removed."""
        self.assertEqual(we.ensure_local_path("/page.html#section"), "page.html")

    def test_nested_path(self) -> None:
        """Nested paths should have only the leading slash stripped."""
        self.assertEqual(we.ensure_local_path("/a/b/c.html"), "a/b/c.html")


# ---------------------------------------------------------------------------
# is_same_site
# ---------------------------------------------------------------------------

class TestIsSameSite(unittest.TestCase):
    """Tests for :func:`wayback_extractor.is_same_site`."""

    def test_exact_match(self) -> None:
        """Exact host match should return True."""
        self.assertTrue(we.is_same_site("https://example.com/page", "example.com"))

    def test_subdomain_match(self) -> None:
        """Subdomain of root_host should return True."""
        self.assertTrue(we.is_same_site("https://blog.example.com/x", "example.com"))

    def test_different_host(self) -> None:
        """A completely different host should return False."""
        self.assertFalse(we.is_same_site("https://other.org/page", "example.com"))

    def test_partial_suffix_not_same_site(self) -> None:
        """A host that merely ends with the root string should not match."""
        self.assertFalse(
            we.is_same_site("https://notexample.com/page", "example.com")
        )

    def test_case_insensitive(self) -> None:
        """Comparison should be case-insensitive."""
        self.assertTrue(we.is_same_site("https://EXAMPLE.COM/", "example.com"))

    def test_http_scheme(self) -> None:
        """HTTP scheme should work the same way as HTTPS."""
        self.assertTrue(we.is_same_site("http://example.com/", "example.com"))


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------

class TestNormalizeUrl(unittest.TestCase):
    """Tests for :func:`wayback_extractor.normalize_url`."""

    def test_passthrough_when_not_ignoring(self) -> None:
        """With ignore_query_params=False the URL is returned unchanged."""
        url = "https://example.com/page?q=1"
        self.assertEqual(we.normalize_url(url, ignore_query_params=False), url)

    def test_strips_query_params_when_flag_set(self) -> None:
        """With ignore_query_params=True the query string is removed."""
        url = "https://example.com/page?q=1&page=2"
        self.assertEqual(
            we.normalize_url(url, ignore_query_params=True),
            "https://example.com/page",
        )

    def test_empty_string_returns_empty(self) -> None:
        """Empty input should be returned unchanged."""
        self.assertEqual(we.normalize_url(""), "")

    def test_url_without_query_unchanged(self) -> None:
        """A URL with no query string should be unchanged regardless of flag."""
        url = "https://example.com/path/to/page"
        self.assertEqual(we.normalize_url(url, ignore_query_params=True), url)


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------

class TestRateLimiter(unittest.TestCase):
    """Tests for :class:`wayback_extractor.RateLimiter`."""

    def test_init_defaults(self) -> None:
        """Default capacity and fill-rate should be applied correctly."""
        rl = we.RateLimiter(rps=1.0, burst=5)
        self.assertEqual(rl.capacity, 5.0)
        self.assertEqual(rl.fill, 1.0)

    def test_min_burst_clamped_to_one(self) -> None:
        """burst values below 1 should be clamped to 1."""
        rl = we.RateLimiter(burst=0)
        self.assertEqual(rl.capacity, 1.0)

    def test_min_rps_clamped(self) -> None:
        """rps values below 0.05 should be clamped to 0.05."""
        rl = we.RateLimiter(rps=0.0)
        self.assertEqual(rl.fill, 0.05)

    def test_take_consumes_token(self) -> None:
        """A single take() call should reduce the token count by 1."""
        rl = we.RateLimiter(rps=100.0, burst=10)
        rl.tokens = 5.0
        rl.take()
        self.assertAlmostEqual(rl.tokens, 4.0, places=1)

    def test_take_sleeps_when_no_tokens(self) -> None:
        """take() must call time.sleep when the bucket is empty."""
        rl = we.RateLimiter(rps=1.0, burst=1)
        rl.tokens = 0.0
        with patch("time.sleep") as mock_sleep:
            rl.take()
            mock_sleep.assert_called_once()

    def test_take_is_thread_safe(self) -> None:
        """Concurrent take() calls must not raise or corrupt state."""
        rl = we.RateLimiter(rps=1000.0, burst=1000)
        errors: list[Exception] = []

        def worker() -> None:
            """Run 10 take() calls and record any exceptions."""
            try:
                for _ in range(10):
                    rl.take()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# origin_ok
# ---------------------------------------------------------------------------

class TestOriginOk(unittest.TestCase):
    """Tests for :func:`wayback_extractor.origin_ok`."""

    def test_2xx_response_is_ok(self) -> None:
        """HTTP 200 responses should be considered OK."""
        resp = _make_response(status_code=200)
        self.assertTrue(we.origin_ok(resp))

    def test_404_response_is_not_ok(self) -> None:
        """HTTP 404 responses should not be considered OK."""
        resp = _make_response(status_code=404)
        self.assertFalse(we.origin_ok(resp))

    def test_archive_header_200_overrides_status(self) -> None:
        """X-Archive-Orig-status: 200 should make the check pass even on a
        non-200 transport status."""
        resp = _make_response(
            status_code=404,
            headers={"X-Archive-Orig-status": "200 OK"},
        )
        self.assertTrue(we.origin_ok(resp))

    def test_archive_header_404_makes_fail(self) -> None:
        """X-Archive-Orig-status: 404 should fail even if transport is 200."""
        resp = _make_response(
            status_code=200,
            headers={"X-Archive-Orig-status": "404 Not Found"},
        )
        self.assertFalse(we.origin_ok(resp))

    def test_archive_header_301_is_not_ok(self) -> None:
        """X-Archive-Orig-status: 301 is outside 2xx so should return False."""
        resp = _make_response(
            status_code=200,
            headers={"X-Archive-Orig-status": "301 Moved"},
        )
        self.assertFalse(we.origin_ok(resp))

    def test_299_is_ok(self) -> None:
        """Status 299 (edge of 2xx range) should be accepted."""
        resp = _make_response(status_code=299)
        self.assertTrue(we.origin_ok(resp))


# ---------------------------------------------------------------------------
# looks_html
# ---------------------------------------------------------------------------

class TestLooksHtml(unittest.TestCase):
    """Tests for :func:`wayback_extractor.looks_html`."""

    def test_text_html_is_html(self) -> None:
        """text/html content-type should return True."""
        resp = _make_response(content_type="text/html; charset=utf-8")
        self.assertTrue(we.looks_html(resp))

    def test_xhtml_is_html(self) -> None:
        """application/xhtml+xml should return True."""
        resp = _make_response(content_type="application/xhtml+xml")
        self.assertTrue(we.looks_html(resp))

    def test_plain_text_is_not_html(self) -> None:
        """text/plain should return False."""
        resp = _make_response(content_type="text/plain")
        self.assertFalse(we.looks_html(resp))

    def test_json_is_not_html(self) -> None:
        """application/json should return False."""
        resp = _make_response(content_type="application/json")
        self.assertFalse(we.looks_html(resp))

    def test_no_content_type_is_not_html(self) -> None:
        """Missing Content-Type should return False."""
        resp = requests.Response()
        resp.headers = requests.structures.CaseInsensitiveDict({})
        self.assertFalse(we.looks_html(resp))

    def test_content_type_containing_html(self) -> None:
        """A content-type string that contains 'html' should return True."""
        resp = _make_response(content_type="text/html+custom")
        self.assertTrue(we.looks_html(resp))


# ---------------------------------------------------------------------------
# latest_per_original
# ---------------------------------------------------------------------------

class TestLatestPerOriginal(unittest.TestCase):
    """Tests for :func:`wayback_extractor.latest_per_original`."""

    def _rec(
        self,
        original: str,
        ts: str,
        status: str = "200",
        mime: str = "text/html",
    ) -> dict[str, Any]:
        """Build a minimal CDX record dict.

        Args:
            original: Original URL.
            ts: 14-digit IA timestamp.
            status: HTTP status code string.
            mime: MIME type string.

        Returns:
            Dict mimicking a CDX row.
        """
        return {
            "original": original,
            "timestamp": ts,
            "statuscode": status,
            "mimetype": mime,
        }

    def test_keeps_latest_snapshot(self) -> None:
        """When two records exist for the same URL the newer one is kept."""
        records = [
            self._rec("http://example.com/", "20230101000000"),
            self._rec("http://example.com/", "20230115000000"),
        ]
        result = we.latest_per_original(records, "20231231235959")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["timestamp"], "20230115000000")

    def test_excludes_records_after_cutoff(self) -> None:
        """Records with timestamps beyond the cutoff are excluded."""
        records = [self._rec("http://example.com/", "20250101000000")]
        result = we.latest_per_original(records, "20231231235959")
        self.assertEqual(result, [])

    def test_prefers_good_over_bad_snapshot(self) -> None:
        """An older non-404 snapshot beats a newer 404."""
        records = [
            self._rec("http://example.com/page", "20230101000000", status="200"),
            self._rec("http://example.com/page", "20230201000000", status="404"),
        ]
        result = we.latest_per_original(records, "20231231235959")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["timestamp"], "20230101000000")

    def test_skips_robots_txt(self) -> None:
        """robots.txt URLs should always be excluded."""
        records = [self._rec("http://example.com/robots.txt", "20230101000000")]
        result = we.latest_per_original(records, "20231231235959")
        self.assertEqual(result, [])

    def test_path_prefix_filter(self) -> None:
        """Records whose path does not start with path_prefix are excluded."""
        records = [
            self._rec("http://example.com/en/page", "20230101000000"),
            self._rec("http://example.com/fr/page", "20230101000000"),
        ]
        result = we.latest_per_original(
            records, "20231231235959", path_prefix="/en/"
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["original"], "http://example.com/en/page")

    def test_non_html_excluded_by_default(self) -> None:
        """Non-HTML MIME types are excluded when include_nonhtml is False."""
        records = [
            self._rec(
                "http://example.com/file.pdf",
                "20230101000000",
                mime="application/pdf",
            )
        ]
        result = we.latest_per_original(
            records, "20231231235959", include_nonhtml=False
        )
        self.assertEqual(result, [])

    def test_non_html_included_when_flag_set(self) -> None:
        """Non-HTML MIME types are included when include_nonhtml is True."""
        records = [
            self._rec(
                "http://example.com/file.pdf",
                "20230101000000",
                mime="application/pdf",
            )
        ]
        result = we.latest_per_original(
            records, "20231231235959", include_nonhtml=True
        )
        self.assertEqual(len(result), 1)

    def test_ignore_query_params_deduplicates(self) -> None:
        """Two URLs differing only in query string collapse to one record."""
        records = [
            self._rec(
                "http://example.com/page?a=1", "20230101000000"
            ),
            self._rec(
                "http://example.com/page?a=2", "20230115000000"
            ),
        ]
        result = we.latest_per_original(
            records,
            "20231231235959",
            ignore_query_params=True,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["timestamp"], "20230115000000")

    def test_multiple_urls_kept_separately(self) -> None:
        """Different URLs should each appear in the result."""
        records = [
            self._rec("http://example.com/", "20230101000000"),
            self._rec("http://example.com/about", "20230101000000"),
        ]
        result = we.latest_per_original(records, "20231231235959")
        self.assertEqual(len(result), 2)

    def test_empty_records_returns_empty(self) -> None:
        """Empty input should produce empty output."""
        self.assertEqual(we.latest_per_original([], "20231231235959"), [])

    def test_only_404s_keeps_newest_404(self) -> None:
        """When every snapshot is a 404 the newest one should be returned."""
        records = [
            self._rec("http://example.com/gone", "20230101000000", status="404"),
            self._rec("http://example.com/gone", "20230201000000", status="404"),
        ]
        result = we.latest_per_original(records, "20231231235959")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["timestamp"], "20230201000000")


# ---------------------------------------------------------------------------
# rewrite_css_urls
# ---------------------------------------------------------------------------

class TestRewriteCssUrls(unittest.TestCase):
    """Tests for :func:`wayback_extractor.rewrite_css_urls`."""

    def test_same_site_url_rewritten(self) -> None:
        """Same-site url() references should be rewritten to relative paths."""
        css = b"body { background: url('http://example.com/img/bg.png'); }"
        result = we.rewrite_css_urls(css, "http://example.com/css/style.css",
                                     "example.com", "css")
        self.assertIn("bg.png", result)
        self.assertNotIn("http://example.com", result)

    def test_third_party_url_untouched(self) -> None:
        """Third-party url() references should remain unchanged."""
        css = b"body { background: url('https://cdn.other.org/img.png'); }"
        result = we.rewrite_css_urls(css, "http://example.com/css/style.css",
                                     "example.com", "css")
        self.assertIn("https://cdn.other.org/img.png", result)

    def test_data_uri_untouched(self) -> None:
        """data: URIs must not be rewritten."""
        css = b"div { background: url('data:image/png;base64,abc'); }"
        result = we.rewrite_css_urls(css, "http://example.com/style.css",
                                     "example.com", "")
        self.assertIn("data:image/png", result)

    def test_hash_url_untouched(self) -> None:
        """Fragment-only urls (#...) must not be rewritten."""
        css = b"div { background: url('#icon'); }"
        result = we.rewrite_css_urls(css, "http://example.com/style.css",
                                     "example.com", "")
        self.assertIn("#icon", result)

    def test_latin1_fallback_decoding(self) -> None:
        """CSS bytes with non-UTF-8 characters should still be processed."""
        css = "body { background: url('/img.png'); }".encode("latin-1")
        # Add a non-UTF-8 byte to force latin-1 decoding path
        css = b"\xff" + css
        result = we.rewrite_css_urls(
            css, "http://example.com/style.css", "example.com", ""
        )
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# rewrite_html_and_collect
# ---------------------------------------------------------------------------

class TestRewriteHtmlAndCollect(unittest.TestCase):
    """Tests for :func:`wayback_extractor.rewrite_html_and_collect`."""

    _BASE_URL = "http://example.com/page.html"
    _ROOT_HOST = "example.com"

    def _rewrite(
        self,
        html: str,
        banner: str | None = None,
        remove_scripts: bool = False,
    ) -> tuple[str, list[str]]:
        """Helper to call rewrite_html_and_collect with common defaults.

        Args:
            html: HTML markup to process.
            banner: Optional banner HTML to inject.
            remove_scripts: Whether to strip all script tags.

        Returns:
            Tuple of (rewritten HTML string, sorted asset URL list).
        """
        return we.rewrite_html_and_collect(
            html.encode("utf-8"),
            self._BASE_URL,
            self._ROOT_HOST,
            banner_html=banner,
            remove_all_scripts=remove_scripts,
        )

    def test_returns_string_and_list(self) -> None:
        """Return types should be (str, list)."""
        html_str, assets = self._rewrite("<html><body>hi</body></html>")
        self.assertIsInstance(html_str, str)
        self.assertIsInstance(assets, list)

    def test_same_site_img_collected(self) -> None:
        """Same-site img src should be added to the assets list."""
        html = (
            '<html><body>'
            '<img src="http://example.com/images/photo.jpg">'
            '</body></html>'
        )
        _, assets = self._rewrite(html)
        self.assertTrue(
            any("photo.jpg" in a for a in assets),
            "Expected photo.jpg in assets",
        )

    def test_third_party_script_removed(self) -> None:
        """Third-party script tags should be stripped from the output."""
        html = (
            '<html><head>'
            '<script src="https://cdn.other.org/tracker.js"></script>'
            '</head><body></body></html>'
        )
        html_str, _ = self._rewrite(html)
        self.assertNotIn("tracker.js", html_str)

    def test_same_site_script_kept(self) -> None:
        """Same-site script src should be preserved (and added to assets)."""
        html = (
            '<html><head>'
            '<script src="http://example.com/js/app.js"></script>'
            '</head><body></body></html>'
        )
        html_str, assets = self._rewrite(html)
        self.assertIn("app.js", html_str)
        self.assertTrue(any("app.js" in a for a in assets))

    def test_remove_all_scripts_flag(self) -> None:
        """With remove_all_scripts=True, even same-site scripts are removed."""
        html = (
            '<html><head>'
            '<script src="http://example.com/js/app.js"></script>'
            '</head><body></body></html>'
        )
        html_str, _ = self._rewrite(html, remove_scripts=True)
        self.assertNotIn("app.js", html_str)

    def test_wayback_toolbar_stripped(self) -> None:
        """The Wayback Machine toolbar div should be removed."""
        html = (
            '<html><body>'
            '<div id="wm-ipp">WB TOOLBAR</div>'
            '<p>Content</p>'
            '</body></html>'
        )
        html_str, _ = self._rewrite(html)
        self.assertNotIn("wm-ipp", html_str)
        self.assertNotIn("WB TOOLBAR", html_str)

    def test_banner_injected_into_body(self) -> None:
        """A banner string should appear inside the body element."""
        html = "<html><body><p>Content</p></body></html>"
        html_str, _ = self._rewrite(html, banner="<div id='banner'>ARCHIVE</div>")
        self.assertIn("ARCHIVE", html_str)

    def test_stylesheet_link_collected(self) -> None:
        """A same-site stylesheet link href should be in assets."""
        html = (
            '<html><head>'
            '<link rel="stylesheet" href="http://example.com/style.css">'
            '</head><body></body></html>'
        )
        _, assets = self._rewrite(html)
        self.assertTrue(any("style.css" in a for a in assets))

    def test_external_stylesheet_not_collected(self) -> None:
        """A third-party stylesheet should NOT be in assets."""
        html = (
            '<html><head>'
            '<link rel="stylesheet" href="https://cdn.other.org/style.css">'
            '</head><body></body></html>'
        )
        _, assets = self._rewrite(html)
        self.assertFalse(any("cdn.other.org" in a for a in assets))

    def test_href_rewritten_to_relative(self) -> None:
        """Anchor href attributes should be rewritten to relative paths."""
        html = (
            '<html><body>'
            '<a href="http://example.com/about.html">About</a>'
            '</body></html>'
        )
        html_str, _ = self._rewrite(html)
        self.assertNotIn("http://example.com/about.html", html_str)

    def test_latin1_bytes_handled(self) -> None:
        """Input bytes that cannot be decoded as UTF-8 must not raise."""
        raw = b"<html><body>caf\xe9</body></html>"
        html_str, assets = we.rewrite_html_and_collect(
            raw, self._BASE_URL, self._ROOT_HOST
        )
        self.assertIsInstance(html_str, str)

    def test_assets_list_is_sorted(self) -> None:
        """Returned assets list should be sorted."""
        html = (
            '<html><head>'
            '<link rel="stylesheet" href="http://example.com/z.css">'
            '<link rel="stylesheet" href="http://example.com/a.css">'
            '</head><body></body></html>'
        )
        _, assets = self._rewrite(html)
        self.assertEqual(assets, sorted(assets))


# ---------------------------------------------------------------------------
# _cdx (mocked)
# ---------------------------------------------------------------------------

class TestCdxHelper(unittest.TestCase):
    """Tests for :func:`wayback_extractor._cdx`."""

    def _mock_session(
        self,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
        raise_exc: Exception | None = None,
    ) -> MagicMock:
        """Build a mock requests.Session whose get() behaves as specified.

        Args:
            status_code: HTTP status code to return.
            json_data: Object to return from resp.json(); overrides text.
            text: Raw response body text when json_data is None.
            raise_exc: If set, get() will raise this exception.

        Returns:
            A :class:`unittest.mock.MagicMock` mimicking requests.Session.
        """
        session = MagicMock()
        if raise_exc:
            session.get.side_effect = raise_exc
            return session

        resp = MagicMock()
        resp.status_code = status_code
        if json_data is not None:
            resp.text = json.dumps(json_data)
            resp.json.return_value = json_data
        else:
            resp.text = text
            resp.json.side_effect = json.JSONDecodeError("err", "", 0)
        resp.raise_for_status.return_value = None
        session.get.return_value = resp
        return session

    def test_returns_rows_on_valid_json(self) -> None:
        """Should parse header+rows into a list of dicts."""
        data = [
            ["timestamp", "original", "statuscode"],
            ["20230101000000", "http://example.com/", "200"],
        ]
        session = self._mock_session(json_data=data)
        result = we._cdx(session, {"output": "json"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["original"], "http://example.com/")

    def test_returns_empty_on_blank_response(self) -> None:
        """An empty response body should return an empty list."""
        session = self._mock_session(text="   ")
        result = we._cdx(session, {})
        self.assertEqual(result, [])

    def test_returns_empty_on_exception(self) -> None:
        """Network exceptions should be caught and return an empty list."""
        session = self._mock_session(raise_exc=Exception("network error"))
        result = we._cdx(session, {})
        self.assertEqual(result, [])

    def test_returns_empty_on_json_only_header(self) -> None:
        """A JSON list with only a header row (no data) returns empty."""
        data: list[Any] = [["timestamp", "original"]]
        session = self._mock_session(json_data=data)
        result = we._cdx(session, {})
        self.assertEqual(result, [])

    def test_returns_empty_on_empty_json_list(self) -> None:
        """An empty JSON list should return an empty list."""
        session = self._mock_session(json_data=[])
        result = we._cdx(session, {})
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# fetch_id / fetch_if (mocked)
# ---------------------------------------------------------------------------

class TestFetchHelpers(unittest.TestCase):
    """Tests for :func:`wayback_extractor.fetch_id` and
    :func:`wayback_extractor.fetch_if`."""

    def _limiter(self) -> we.RateLimiter:
        """Return a high-throughput RateLimiter so tests don't sleep.

        Returns:
            A :class:`RateLimiter` with a very high rps to avoid delays.
        """
        return we.RateLimiter(rps=1000.0, burst=1000)

    def test_fetch_id_success(self) -> None:
        """A successful session.get() should return the response."""
        session = MagicMock()
        resp = _make_response(status_code=200)
        session.get.return_value = resp
        limiter = self._limiter()
        result = we.fetch_id(session, limiter, "20230101000000",
                             "http://example.com/")
        self.assertEqual(result.status_code, 200)

    def test_fetch_id_timeout_returns_504(self) -> None:
        """A timeout should produce a synthetic 504 response."""
        session = MagicMock()
        session.get.side_effect = requests.exceptions.Timeout
        limiter = self._limiter()
        result = we.fetch_id(session, limiter, "20230101000000",
                             "http://example.com/")
        self.assertEqual(result.status_code, 504)

    def test_fetch_id_generic_exception_returns_500(self) -> None:
        """Any other exception should produce a synthetic 500 response."""
        session = MagicMock()
        session.get.side_effect = Exception("boom")
        limiter = self._limiter()
        result = we.fetch_id(session, limiter, "20230101000000",
                             "http://example.com/")
        self.assertEqual(result.status_code, 500)

    def test_fetch_if_success(self) -> None:
        """fetch_if should use the if_ modifier and return the response."""
        session = MagicMock()
        resp = _make_response(status_code=200)
        session.get.return_value = resp
        limiter = self._limiter()
        result = we.fetch_if(session, limiter, "20230101000000",
                             "http://example.com/")
        self.assertEqual(result.status_code, 200)
        call_url = session.get.call_args[0][0]
        self.assertIn("if_", call_url)

    def test_fetch_if_timeout_returns_504(self) -> None:
        """fetch_if timeout should produce a synthetic 504 response."""
        session = MagicMock()
        session.get.side_effect = requests.exceptions.Timeout
        limiter = self._limiter()
        result = we.fetch_if(session, limiter, "20230101000000",
                             "http://example.com/")
        self.assertEqual(result.status_code, 504)

    def test_fetch_id_uses_id_modifier_in_url(self) -> None:
        """fetch_id must use the id_ modifier in the constructed URL."""
        session = MagicMock()
        resp = _make_response(200)
        session.get.return_value = resp
        limiter = self._limiter()
        we.fetch_id(session, limiter, "20230101000000", "http://example.com/")
        call_url = session.get.call_args[0][0]
        self.assertIn("id_", call_url)


# ---------------------------------------------------------------------------
# make_session
# ---------------------------------------------------------------------------

class TestMakeSession(unittest.TestCase):
    """Tests for :func:`wayback_extractor.make_session`."""

    def test_returns_requests_session(self) -> None:
        """make_session() must return a requests.Session instance."""
        session = we.make_session()
        self.assertIsInstance(session, requests.Session)

    def test_user_agent_set(self) -> None:
        """The User-Agent header should contain the project identifier."""
        session = we.make_session()
        ua = session.headers.get("User-Agent", "")
        self.assertIn("WaybackStaticMirror", ua)

    def test_https_adapter_mounted(self) -> None:
        """An HTTPAdapter should be mounted for https://."""
        from requests.adapters import HTTPAdapter
        session = we.make_session()
        adapter = session.get_adapter("https://web.archive.org/")
        self.assertIsInstance(adapter, HTTPAdapter)


# ---------------------------------------------------------------------------
# cdx_history_for_url (mocked)
# ---------------------------------------------------------------------------

class TestCdxHistoryForUrl(unittest.TestCase):
    """Tests for :func:`wayback_extractor.cdx_history_for_url`."""

    def test_delegates_to_cdx(self) -> None:
        """cdx_history_for_url should pass the right parameters to _cdx."""
        data = [
            ["timestamp", "original", "mimetype", "statuscode", "digest",
             "length"],
            ["20230101000000", "http://example.com/", "text/html", "200",
             "sha1:abc", "1024"],
        ]
        session = MagicMock()
        resp = MagicMock()
        resp.text = json.dumps(data)
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        session.get.return_value = resp

        result = we.cdx_history_for_url(
            session, "http://example.com/", "20231231235959"
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["original"], "http://example.com/")
        # Verify the CDX call included the expected URL parameter
        call_params = session.get.call_args[1]["params"]
        self.assertEqual(call_params["url"], "http://example.com/")

    def test_returns_empty_on_network_error(self) -> None:
        """Network failure should return an empty list without raising."""
        session = MagicMock()
        session.get.side_effect = Exception("timeout")
        result = we.cdx_history_for_url(
            session, "http://example.com/", "20231231235959"
        )
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# pick_best_snapshot (mocked)
# ---------------------------------------------------------------------------

class TestPickBestSnapshot(unittest.TestCase):
    """Tests for :func:`wayback_extractor.pick_best_snapshot`."""

    def _limiter(self) -> we.RateLimiter:
        """Return a high-throughput RateLimiter so tests don't sleep.

        Returns:
            A :class:`RateLimiter` with very high rps.
        """
        return we.RateLimiter(rps=1000.0, burst=1000)

    def _record(self, ts: str = "20230101000000") -> dict[str, str]:
        """Return a minimal CDX record dict.

        Args:
            ts: Timestamp for the record.

        Returns:
            CDX-like record dict.
        """
        return {
            "timestamp": ts,
            "original": "http://example.com/",
            "mimetype": "text/html",
            "statuscode": "200",
        }

    def test_returns_best_snapshot_on_200_html(self) -> None:
        """A 200 HTML response should be selected and its content returned."""
        records = [self._record()]
        html_content = b"<html><body>hello</body></html>"
        good_resp = _make_response(200, "text/html", "")
        good_resp._content = html_content

        with patch.object(we, "fetch_id", return_value=good_resp):
            chosen, content = we.pick_best_snapshot(
                records, MagicMock(), self._limiter()
            )
        self.assertIsNotNone(chosen)
        self.assertEqual(content, html_content)

    def test_returns_none_when_no_records(self) -> None:
        """An empty records list should return (None, None)."""
        chosen, content = we.pick_best_snapshot(
            [], MagicMock(), self._limiter()
        )
        self.assertIsNone(chosen)
        self.assertIsNone(content)

    def test_skips_non_200_snapshot(self) -> None:
        """A 404 response should be skipped; result is (None, None) when no
        other candidates exist."""
        records = [self._record()]
        bad_resp = _make_response(404, "text/html", "Not Found")

        with patch.object(we, "fetch_id", return_value=bad_resp):
            chosen, content = we.pick_best_snapshot(
                records, MagicMock(), self._limiter()
            )
        self.assertIsNone(chosen)

    def test_falls_back_to_if_on_ssl_error(self) -> None:
        """An SSLError on fetch_id should cause a retry via fetch_if."""
        records = [self._record()]
        html_content = b"<html><body>ok</body></html>"
        good_resp = _make_response(200, "text/html", "")
        good_resp._content = html_content

        with (
            patch.object(
                we, "fetch_id",
                side_effect=requests.exceptions.SSLError("ssl fail")
            ),
            patch.object(we, "fetch_if", return_value=good_resp),
        ):
            chosen, content = we.pick_best_snapshot(
                records, MagicMock(), self._limiter()
            )
        self.assertIsNotNone(chosen)

    def test_picks_newest_among_multiple(self) -> None:
        """The newest valid snapshot is preferred over older ones."""
        records = [
            self._record("20230101000000"),
            self._record("20230601000000"),
        ]
        html_content = b"<html><body>newest</body></html>"
        good_resp = _make_response(200, "text/html", "")
        good_resp._content = html_content

        calls: list[str] = []

        def fake_fetch_id(
            session: Any,
            limiter: Any,
            ts: str,
            original: str,
            **kwargs: Any,
        ) -> requests.Response:
            """Record which timestamp was fetched and return a good response."""
            calls.append(ts)
            return good_resp

        with patch.object(we, "fetch_id", side_effect=fake_fetch_id):
            chosen, _ = we.pick_best_snapshot(
                records, MagicMock(), self._limiter()
            )
        # The first call should be the newest timestamp
        self.assertEqual(calls[0], "20230601000000")
        self.assertEqual(chosen["timestamp"], "20230601000000")


# ---------------------------------------------------------------------------
# check_availability_api (mocked)
# ---------------------------------------------------------------------------

class TestCheckAvailabilityApi(unittest.TestCase):
    """Tests for :func:`wayback_extractor.check_availability_api`."""

    def _session_returning(self, payload: dict[str, Any]) -> MagicMock:
        """Return a mock session whose get() returns the given JSON payload.

        Args:
            payload: Dict to serialise as the response body.

        Returns:
            Mock session.
        """
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload
        session.get.return_value = resp
        return session

    def _available_payload(self, url: str = "http://example.com/") -> dict:
        """Build a minimal 'available' availability API response.

        Args:
            url: Snapshot URL to embed in the response.

        Returns:
            Dict mimicking the Wayback availability API JSON.
        """
        return {
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": url,
                    "timestamp": "20230101120000",
                    "status": "200",
                }
            }
        }

    def test_returns_result_when_available(self) -> None:
        """An available snapshot should produce a non-empty result list."""
        session = self._session_returning(self._available_payload())
        results = we.check_availability_api(
            session, "example.com", "20231231235959"
        )
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["timestamp"], "20230101120000")

    def test_returns_empty_when_not_available(self) -> None:
        """A payload with no 'closest' key should produce an empty list."""
        session = self._session_returning({"archived_snapshots": {}})
        results = we.check_availability_api(
            session, "example.com", "20231231235959"
        )
        self.assertEqual(results, [])

    def test_returns_empty_on_exception(self) -> None:
        """Network exceptions should return an empty list."""
        session = MagicMock()
        session.get.side_effect = Exception("network error")
        results = we.check_availability_api(
            session, "example.com", "20231231235959"
        )
        self.assertEqual(results, [])

    def test_adds_www_prefix_variant(self) -> None:
        """Both bare domain and www. variant should be queried."""
        session = self._session_returning(self._available_payload())
        we.check_availability_api(session, "example.com", "20231231235959")
        # Two calls: one for example.com, one for www.example.com
        self.assertEqual(session.get.call_count, 2)
        urls_called = [str(c[0][0]) for c in session.get.call_args_list]
        self.assertTrue(any("www.example.com" in u for u in urls_called))


# ---------------------------------------------------------------------------
# _cdx_multi_endpoint (mocked)
# ---------------------------------------------------------------------------

class TestCdxMultiEndpoint(unittest.TestCase):
    """Tests for :func:`wayback_extractor._cdx_multi_endpoint`."""

    def test_combines_results_from_multiple_calls(self) -> None:
        """Results from the prefix call and main endpoint are merged."""
        row = {
            "timestamp": "20230101000000",
            "original": "http://example.com/",
            "statuscode": "200",
            "mimetype": "text/html",
            "digest": "",
            "length": "",
        }

        with patch.object(we, "_cdx", return_value=[row]) as mock_cdx:
            results = we._cdx_multi_endpoint(
                MagicMock(), {"output": "json"}
            )
        self.assertGreater(len(results), 0)
        # _cdx should be called at least twice (prefix + primary endpoint)
        self.assertGreaterEqual(mock_cdx.call_count, 2)

    def test_tries_alternate_endpoint_when_few_results(self) -> None:
        """The alternate CDX endpoint is tried when results < 10."""
        row = {
            "timestamp": "20230101000000",
            "original": "http://example.com/",
            "statuscode": "200",
            "mimetype": "text/html",
            "digest": "",
            "length": "",
        }
        call_count: list[int] = [0]

        def fake_cdx(
            session: Any,
            params: dict,
            timeout: int = 90,
            endpoint: str = we.CDX,
        ) -> list[dict]:
            """Return one row on every call and record the endpoint used."""
            call_count[0] += 1
            return [row]

        with patch.object(we, "_cdx", side_effect=fake_cdx):
            we._cdx_multi_endpoint(MagicMock(), {"output": "json"})
        # Should have been called at least 3 times (prefix, primary, alternate)
        self.assertGreaterEqual(call_count[0], 3)


# ---------------------------------------------------------------------------
# download_asset (mocked, uses tmp dir)
# ---------------------------------------------------------------------------

class TestDownloadAsset(unittest.TestCase):
    """Tests for :func:`wayback_extractor.download_asset`."""

    def _limiter(self) -> we.RateLimiter:
        """Return a high-throughput RateLimiter so tests don't sleep.

        Returns:
            :class:`RateLimiter` with very high rps.
        """
        return we.RateLimiter(rps=1000.0, burst=1000)

    def test_successful_download_writes_file(self) -> None:
        """A 200 response should write the asset to disk and return ok=True."""
        import tempfile

        content = b"body { color: red; }"
        resp = _make_response(200, "text/css", "")
        resp._content = content
        resp.iter_content = lambda chunk_size: iter([content])

        session = MagicMock()
        with (
            patch.object(we, "fetch_id", return_value=resp),
            tempfile.TemporaryDirectory() as outdir,
        ):
            local, ok, out_path, ctype = we.download_asset(
                session,
                self._limiter(),
                "20230101000000",
                "http://example.com/style.css",
                outdir,
            )
        self.assertTrue(ok)
        self.assertIn("style.css", local)

    def test_falls_back_to_fetch_if_on_non_200(self) -> None:
        """When fetch_id fails, download_asset retries with fetch_if."""
        import tempfile

        content = b"body {}"
        bad_resp = _make_response(404)
        good_resp = _make_response(200, "text/css", "")
        good_resp._content = content
        good_resp.iter_content = lambda chunk_size: iter([content])

        session = MagicMock()
        with (
            patch.object(we, "fetch_id", return_value=bad_resp),
            patch.object(we, "fetch_if", return_value=good_resp),
            tempfile.TemporaryDirectory() as outdir,
        ):
            _, ok, _, _ = we.download_asset(
                session,
                self._limiter(),
                "20230101000000",
                "http://example.com/style.css",
                outdir,
            )
        self.assertTrue(ok)

    def test_returns_ok_false_on_all_failures(self) -> None:
        """When both fetchers fail, ok=False is returned."""
        import tempfile

        bad_resp = _make_response(500)

        session = MagicMock()
        with (
            patch.object(we, "fetch_id", return_value=bad_resp),
            patch.object(we, "fetch_if", return_value=bad_resp),
            tempfile.TemporaryDirectory() as outdir,
        ):
            _, ok, _, _ = we.download_asset(
                session,
                self._limiter(),
                "20230101000000",
                "http://example.com/missing.css",
                outdir,
            )
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
