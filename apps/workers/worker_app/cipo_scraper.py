"""CIPO CPD scrape engine — ported near-verbatim from the legacy /opt/cipo-monitor service
(WP 6.2). This core is proven in daily production against 800+ applications; every quirk below
is hard-won CIPO behaviour. Change the plumbing around it, not the flow inside it.

Preserved verbatim from legacy `cipo_scraper.py` / `captcha_solver.py`:
  * number-search flow on .../cpd/eng/search/number.html (headless Chromium via Playwright);
  * the Referer-header workaround — CIPO's edge 308-redirects referer-less deep requests to a
    dead origin IP, so EVERY CIPO navigation sends a Referer;
  * the summary-URL fallback — some number-search HTTP 500s are CPD flakiness, not dead
    records, so retry the direct per-patent summary page before giving up;
  * 2Captcha reCAPTCHA v2 solving for the download modal (one solve per combined PDF);
  * retry budget + throttle-with-jitter (CIPO is rate-sensitive and flaky);
  * combined-PDF download of all selected new docs per application.

Replaced: module-level config/env → CipoScraperConfig; the 2Captcha key arrives through the
orchestrator credential broker (D10), never from a .env file.
"""
from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from py_shared.domain.watchers import DocRow

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 30_000
DOWNLOAD_TIMEOUT_MS = 120_000


class CipoError(Exception):
    pass


class CaptchaError(Exception):
    pass


@dataclass
class CipoScraperConfig:
    twocaptcha_api_key: str = ""
    headless: bool = True
    download_dir: Path = Path("./downloads")
    # Number Search page: has the number input + "View Data" button. (basic.html is a keyword
    # search with different markup.)
    search_url: str = "https://brevets-patents.ic.gc.ca/opic-cipo/cpd/eng/search/number.html"
    # Direct per-patent summary page — the 500-fallback second opinion.
    summary_url_template: str = (
        "https://brevets-patents.ic.gc.ca/opic-cipo/cpd/eng/patent/{num}/summary.html"
    )
    # CIPO edge redirects referer-less deep requests (308) to a dead origin IP. Any Referer
    # header yields 200, so every CIPO goto sends one.
    referer: str = "https://www.google.com/"
    page_delay_seconds: float = 1.0
    download_delay_seconds: float = 1.0
    jitter_min_seconds: float = 0.0
    jitter_max_seconds: float = 0.5
    max_search_attempts: int = 3
    max_download_attempts: int = 3
    launch_args: list[str] = field(default_factory=lambda: [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
    ])


class CaptchaSolver:
    """2Captcha reCAPTCHA v2 solver (legacy captcha_solver.py)."""

    def __init__(self, api_key: str):
        if not api_key:
            raise CaptchaError("2Captcha API key missing (broker slot cipo/twocaptcha-api-key)")
        from twocaptcha import TwoCaptcha  # runtime dep of the workers app only

        self.solver = TwoCaptcha(api_key)

    def solve_recaptcha_v2(self, sitekey: str, page_url: str, invisible: bool = False) -> str:
        try:
            result = self.solver.recaptcha(
                sitekey=sitekey, url=page_url, invisible=1 if invisible else 0,
            )
        except Exception as e:
            raise CaptchaError(f"2Captcha solve failed: {e}") from e
        token = result.get("code") if isinstance(result, dict) else None
        if not token:
            raise CaptchaError(f"2Captcha returned no token: {result!r}")
        return str(token)


class CipoScraper:
    """Context-managed Playwright session against the CIPO CPD site."""

    def __init__(self, config: CipoScraperConfig):
        self.config = config
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._solver: CaptchaSolver | None = None

    def __enter__(self) -> CipoScraper:
        from playwright.sync_api import sync_playwright  # heavyweight; import at session start

        self._pw = sync_playwright().start()
        launch_kwargs: dict[str, Any] = dict(
            headless=self.config.headless, args=list(self.config.launch_args),
        )
        try:
            self._browser = self._pw.chromium.launch(channel="chrome", **launch_kwargs)
            log.info("Launched installed Chrome")
        except Exception:
            log.warning("Installed Chrome not found; falling back to bundled Chromium")
            self._browser = self._pw.chromium.launch(**launch_kwargs)
        self._context = self._browser.new_context(
            accept_downloads=True,
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1400, "height": 900},
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    @property
    def page(self) -> Any:
        assert self._page is not None
        return self._page

    @property
    def solver(self) -> CaptchaSolver:
        if self._solver is None:
            self._solver = CaptchaSolver(self.config.twocaptcha_api_key)
        return self._solver

    # --- navigation ----------------------------------------------------------

    def _goto_with_retry(
        self, url: str, retries: int = 3, backoff: float = 10.0, referer: str | None = None,
    ) -> None:
        # CIPO edge returns 308 -> dead origin IP when the Referer header is absent. Sending
        # any referer yields 200; a fresh page.goto() to a deep URL otherwise sends none.
        ref = referer if referer is not None else self.config.referer
        for attempt in range(1, retries + 1):
            try:
                self.page.goto(url, wait_until="domcontentloaded", referer=ref)
                return
            except Exception as e:
                msg = str(e).split("\n")[0]
                if attempt < retries:
                    log.warning("CIPO goto attempt %d/%d failed (%s), retrying in %.0fs",
                                attempt, retries, msg, backoff)
                    time.sleep(backoff)
                else:
                    raise CipoError(f"CIPO unreachable after {retries} attempts: {msg}") from e

    def _is_error_page(self) -> bool:
        try:
            return "/error/internal.html" in str(self.page.url)
        except Exception:
            return False

    def _try_summary_url(self, app_num: str) -> bool:
        """500-fallback: load the direct CPD summary page. True if it rendered (not the error
        page). Some number-search 500s are CPD flakiness rather than a broken record."""
        url = self.config.summary_url_template.format(num=app_num)
        try:
            self._goto_with_retry(url, retries=2)
        except Exception as e:
            log.warning("Summary-URL fallback failed for %s: %s",
                        app_num, str(e).split(chr(10))[0])
            return False
        if self._is_error_page():
            log.warning("Summary-URL fallback also 500'd for %s", app_num)
            return False
        log.info("Summary-URL fallback succeeded for %s", app_num)
        return True

    # --- search --------------------------------------------------------------

    def search_application(self, app_num: str) -> list[DocRow]:
        """Return ALL document rows for an application, newest first (table order).

        Empty list = page loaded but the documents table had no rows. Raises CipoError on an
        unrecoverable CIPO 500 (after the summary-URL fallback also fails)."""
        app_num = app_num.replace(",", "").replace(" ", "").strip()
        page = self.page

        self._goto_with_retry(self.config.search_url)

        input_loc = self._find_number_input()
        input_loc.fill("")
        input_loc.fill(app_num)
        self._find_view_data_button().click()
        page.wait_for_load_state("domcontentloaded")

        if self._is_error_page():
            log.warning("CIPO 500 on number-search for %s; trying direct summary URL", app_num)
            if not self._try_summary_url(app_num):
                raise CipoError(f"CIPO server error 500 (record unavailable) for {app_num}")

        try:
            page.wait_for_selector("text=/Patent Summary|Sommaire/i", timeout=DEFAULT_TIMEOUT_MS)
        except Exception:
            log.warning("Patent Summary header not detected for %s", app_num)

        self._open_documents_tab()
        return self._read_all_document_rows()

    def _find_number_input(self) -> Any:
        page = self.page
        candidates: list[Callable[[], Any]] = [
            lambda: page.get_by_label(re.compile(r"Search Patent Number|Patent Number", re.I)),
            lambda: page.get_by_placeholder(re.compile(r"Patent Number", re.I)),
            lambda: page.locator("input#patentNumber"),
            lambda: page.locator("input[name='patentNumber']"),
            lambda: page.locator("input#query"),
            lambda: page.locator("input[name='query']"),
            lambda: page.locator("input[type='text']").first,
        ]
        for build_loc in candidates:
            try:
                loc = build_loc()
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        raise CipoError("Could not locate Patent Number search input")

    def _find_view_data_button(self) -> Any:
        page = self.page
        candidates: list[Callable[[], Any]] = [
            lambda: page.get_by_role("button", name=re.compile(r"View Data", re.I)),
            lambda: page.locator("input[type='submit'][value*='View']"),
            lambda: page.locator("button:has-text('View Data')"),
        ]
        for build_loc in candidates:
            try:
                loc = build_loc()
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        raise CipoError("Could not locate View Data button")

    # --- documents table -----------------------------------------------------

    def _open_documents_tab(self) -> None:
        # CIPO summary uses WET <details role=tab> panels. The Documents panel is collapsed by
        # default; while collapsed its table cells return empty inner_text. Activate the tab,
        # force the <details> open as a fallback, then wait for rows to actually populate.
        page = self.page
        candidates: list[Callable[[], Any]] = [
            lambda: page.get_by_role("tab", name=re.compile(r"^\s*Documents\s*$", re.I)),
            lambda: page.get_by_role("tab", name=re.compile(r"Documents", re.I)),
            lambda: page.locator("summary:has-text('Documents')"),
            lambda: page.get_by_role("button", name=re.compile(r"^Documents$", re.I)),
        ]
        for build_loc in candidates:
            try:
                loc = build_loc()
                if loc.count() > 0:
                    loc.first.scroll_into_view_if_needed()
                    loc.first.click()
                    break
            except Exception:
                continue
        else:
            log.warning("Documents tab control not found — table may already be visible")

        try:
            page.evaluate(
                """() => {
                    document.querySelectorAll('details').forEach(d => {
                        const s = d.querySelector('summary');
                        if (s && /^\\s*Documents\\s*$/i.test(s.innerText)) d.open = true;
                    });
                }"""
            )
        except Exception:
            pass

        try:
            page.wait_for_function(
                """() => {
                    const r = document.querySelector('#documentsTable tbody tr');
                    return r && r.innerText.trim().length > 0;
                }""",
                timeout=DEFAULT_TIMEOUT_MS,
            )
        except Exception:
            log.warning("Documents table rows did not populate in time")

    @staticmethod
    def parse_doc_row(texts: list[str]) -> DocRow:
        """Best-effort (date, description) extraction from a documents-table row's cell texts."""
        description = ""
        date_str = ""
        for t in texts:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
                date_str = t
            elif not description and t and not re.fullmatch(r"\d+", t):
                description = t
        if not date_str:
            for t in texts:
                m = re.search(r"\d{4}-\d{2}-\d{2}", t)
                if m:
                    date_str = m.group(0)
                    break
        if not description and texts:
            description = texts[0]
        return DocRow(description=description, date_str=date_str)

    def _read_all_document_rows(self) -> list[DocRow]:
        table = self._find_documents_table()
        if table is None:
            return []
        rows = table.locator("tbody tr")
        docs: list[DocRow] = []
        for r in range(rows.count()):
            cells = rows.nth(r).locator("td")
            if cells.count() < 2:
                continue
            texts = [cells.nth(i).inner_text().strip() for i in range(cells.count())]
            docs.append(self.parse_doc_row(texts))
        return docs

    def _find_documents_table(self) -> Any:
        page = self.page
        candidates = [
            "table#documentsTable",
            "table:has(th:has-text('Document Description'))",
            "table:has(th:has-text('Description'))",
            "table:has-text('Document Description')",
            "[role='region']:has-text('Documents') table",
        ]
        for sel in candidates:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        return None

    def _doc_checkboxes(self) -> list[Any]:
        """One checkbox locator per documents-table row, in table order; None placeholders keep
        positional indices aligned with _read_all_document_rows output."""
        table = self._find_documents_table()
        if table is None:
            raise CipoError("Documents table not found for checkbox")
        rows = table.locator("tbody tr")
        n = rows.count()
        if n == 0:
            raise CipoError("Documents table empty")
        out: list[Any] = []
        for r in range(n):
            cells = rows.nth(r).locator("td")
            if cells.count() < 2:
                continue
            cb = rows.nth(r).locator("input[type='checkbox']").first
            out.append(cb if cb.count() > 0 else None)
        return out

    # --- download (one combined PDF, one CAPTCHA solve) -----------------------

    def download_documents(
        self, app_num: str, indices: list[int] | None = None, dest_dir: Path | None = None,
    ) -> Path:
        """Download one or more document rows as a single combined PDF. ``indices`` are 0-based
        positions in the documents table (same order as search_application's list)."""
        dest = Path(dest_dir or self.config.download_dir)
        dest.mkdir(parents=True, exist_ok=True)
        page = self.page

        checkboxes = self._doc_checkboxes()
        if not indices:
            indices = [0]
        checked = 0
        for i in indices:
            if 0 <= i < len(checkboxes) and checkboxes[i] is not None:
                checkboxes[i].check()
                checked += 1
        if checked == 0:
            # indices all out of range / no checkbox — fall back to the first available row so
            # we never silently download nothing.
            for cb in checkboxes:
                if cb is not None:
                    cb.check()
                    break
            else:
                raise CipoError("No selectable document checkbox found")
            log.warning("download_documents: indices %s unusable for %s; used first available",
                        indices, app_num)

        # The "Download Selected as Single PDF" control is an <a> (role=link), not a button; a
        # plain text= locator also matches an instructions <p> that quotes the phrase.
        download_btn = page.get_by_role(
            "link", name=re.compile(r"Download Selected.*Single PDF", re.I)
        )
        if download_btn.count() == 0:
            download_btn = page.get_by_role(
                "button", name=re.compile(r"Download Selected.*Single PDF", re.I)
            )
        if download_btn.count() == 0:
            download_btn = page.locator(
                "a:has-text('Download Selected as Single PDF'), "
                "button:has-text('Download Selected as Single PDF'), "
                "input[value*='Download Selected as Single PDF']"
            )
        download_btn.first.click()

        self._solve_modal_recaptcha()

        modal_download = page.get_by_role(
            "button", name=re.compile(r"^\s*Download documents?\s*$", re.I)
        )
        if modal_download.count() == 0:
            modal_download = page.locator(
                "input[value*='Download documents'], "
                "button:has-text('Download documents'), "
                "a:has-text('Download documents')"
            )

        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
            modal_download.first.click()
        download = dl_info.value

        suggested = download.suggested_filename or f"PDF_{app_num}.pdf"
        ext = Path(suggested).suffix or ".pdf"
        out_path = dest / f"PDF_{app_num}{ext}"
        download.save_as(str(out_path))
        return out_path

    def _solve_modal_recaptcha(self) -> None:
        page = self.page
        try:
            page.wait_for_selector(
                "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], "
                "iframe[src*='turnstile'], div.g-recaptcha, div.h-captcha, .cf-turnstile",
                timeout=DEFAULT_TIMEOUT_MS,
            )
        except Exception:
            log.info("No CAPTCHA element detected in modal — skipping solve")
            return

        sitekey = self._extract_sitekey()
        if not sitekey:
            raise CipoError("reCAPTCHA sitekey not found in modal")

        token = self.solver.solve_recaptcha_v2(sitekey=sitekey, page_url=str(page.url))
        page.evaluate(
            """(token) => {
                const ta = document.getElementById('g-recaptcha-response');
                if (ta) { ta.style.display='block'; ta.innerHTML = token; ta.value = token; }
                document.querySelectorAll('textarea[name="g-recaptcha-response"]').forEach(t => {
                    t.style.display='block'; t.innerHTML = token; t.value = token;
                });
                if (typeof window.___grecaptcha_cfg !== 'undefined') {
                    const cfg = window.___grecaptcha_cfg.clients;
                    Object.keys(cfg).forEach(k => {
                        const client = cfg[k];
                        const walk = (obj) => {
                            if (!obj || typeof obj !== 'object') return;
                            Object.keys(obj).forEach(key => {
                                const v = obj[key];
                                if (typeof v === 'function' && v.toString().includes('callback')) {
                                    try { v(token); } catch (e) {}
                                }
                                if (v && typeof v === 'object') walk(v);
                            });
                        };
                        walk(client);
                    });
                }
            }""",
            token,
        )
        page.wait_for_timeout(500)

    def _extract_sitekey(self) -> str | None:
        page = self.page
        try:
            el = page.locator("div.g-recaptcha[data-sitekey]").first
            if el.count() > 0:
                sk = el.get_attribute("data-sitekey")
                if sk:
                    return str(sk)
        except Exception:
            pass
        try:
            iframe = page.locator("iframe[src*='recaptcha']").first
            if iframe.count() > 0:
                src = iframe.get_attribute("src") or ""
                m = re.search(r"[?&]k=([\w-]+)", src)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return None


def make_throttle(config: CipoScraperConfig) -> Any:
    """Inter-request sleep with jitter (CIPO is rate-sensitive). Returns sleep(seconds=None)."""

    def sleep_throttle(seconds: float | None = None) -> None:
        base = seconds if seconds is not None else config.page_delay_seconds
        jitter = random.uniform(config.jitter_min_seconds, config.jitter_max_seconds)
        time.sleep(base + jitter)

    return sleep_throttle
