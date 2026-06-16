"""
Security UI integration tests for the LANraragi server.

Stored-XSS regression suite covering category names, archive tags, and ToC titles rendered across
the index, reader, upload, batch, and stats pages. Each test plants a payload through a normal API,
opens the page that renders it, and asserts the payload does not execute.

All tests are xfail(strict=False): they report xfail without the LRR-side fix and XPASS once built
(--build) against a branch that escapes the value. Drop the xfail marker per finding as each fix merges.
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path

import playwright.async_api
import playwright.async_api._generated
import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import AddTocEntryRequest
from lanraragi.models.category import CreateCategoryRequest

from aio_lanraragi_tests.common import DEFAULT_LRR_PASSWORD
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import (
    add_archive_to_category,
    create_archive_file,
    trigger_stat_rebuild,
    upload_archive,
)
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
    switch_display_mode,
)

LOGGER = logging.getLogger(__name__)

CANARY_HOST = "xss-canary.invalid"
CANARY_URL = f"//{CANARY_HOST}/fired"
CANARY_MARKER = "xss_fired_marker"

MARKUP_PAYLOAD = (
    "</script></option></select>"
    f'<img src="{CANARY_URL}" onerror="console.error(\'{CANARY_MARKER}\')">'
    f"<svg onload=\"console.error('{CANARY_MARKER}')\"></svg>"
)

# Attribute-breakout payload for HTML-attribute / quoted-JS-string sinks. Uses a relative src so it
# carries no slash (safe inside a filename) and fires the console marker without a network request.
# The [onerror] DOM count assertion is also omitted — some pages carry legitimate onerror handlers.
ATTR_PAYLOAD = f"\"><img src=x onerror=\"console.error('{CANARY_MARKER}')\">"


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: category name not safely embedded in the catList inline script (templates/index.html.tt2:61, JS-string context)",
    strict=False,
)
async def test_category_name_escape_index(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — a category name must not execute when the index page builds its catList script.

    1. Create a category whose name is an XSS payload.
    2. Open the index page.
    3. Assert no canary request fired, no console marker logged, no live onerror/onload attribute.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> CREATE CATEGORY STAGE >>>>>
    _, error = await lrr_client.category_api.create_category(CreateCategoryRequest(name=MARKUP_PAYLOAD))
    assert not error, f"Failed to create category (status {error.status}): {error.error}"
    # <<<<< CREATE CATEGORY STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            canary_requests: list[str] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))
            page.on("request", lambda request: canary_requests.append(request.url) if CANARY_HOST in request.url else None)

            await page.goto(lrr_client.lrr_base_url, timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            assert not canary_requests, f"Canary request fired from index catList: {canary_requests}"
            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired in console: {evt.text}"
            assert await page.locator("[onerror]").count() == 0, "Live onerror attribute injected on index page"
            assert await page.locator("[onload]").count() == 0, "Live onload attribute injected on index page"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: category name not HTML-escaped in templates/reader.html.tt2:113 (label span) and :127 (option)",
    strict=False,
)
async def test_category_name_escape_reader(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — a category name must not execute on the reader page (archive's own categories and
    the full category dropdown).

    1. Create a category whose name is an XSS payload, upload an archive, add it to the category.
    2. Log in (the category section renders only for logged-in users), then open the reader.
    3. Assert no canary request fired, no console marker logged, no live onerror/onload attribute.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    response, error = await lrr_client.category_api.create_category(CreateCategoryRequest(name=MARKUP_PAYLOAD))
    assert not error, f"Failed to create category (status {error.status}): {error.error}"
    category_id = response.category_id

    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = create_archive_file(Path(tmpdir), "archive_1", 3)
        response, error = await upload_archive(lrr_client, save_path, save_path.name, semaphore, title="Title 1", tags="")
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid

    _, error = await add_archive_to_category(lrr_client, category_id, arcid, semaphore)
    assert not error, f"Failed to add archive to category (status {error.status}): {error.error}"
    # <<<<< CREATE & UPLOAD ARCHIVES <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            canary_requests: list[str] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))
            page.on("request", lambda request: canary_requests.append(request.url) if CANARY_HOST in request.url else None)

            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")

            await page.goto(f"{lrr_client.lrr_base_url}/reader?id={arcid}", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            assert not canary_requests, f"Canary request fired on reader page: {canary_requests}"
            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired in console: {evt.text}"
            assert await page.locator("[onerror]").count() == 0, "Live onerror attribute injected on reader page"
            assert await page.locator("[onload]").count() == 0, "Live onload attribute injected on reader page"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: category name not HTML-escaped in templates/upload.html.tt2:51 and templates/batch.html.tt2:124 (category option)",
    strict=False,
)
async def test_category_name_escape_login_pages(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — a category name must not execute in the category dropdown on the (login-gated)
    upload and batch pages.

    1. Create a category whose name is an XSS payload.
    2. Log in, then open the upload page and the batch page in turn.
    3. On each page assert no canary request fired, no console marker logged, no live onerror/onload.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> CREATE CATEGORY STAGE >>>>>
    _, error = await lrr_client.category_api.create_category(CreateCategoryRequest(name=MARKUP_PAYLOAD))
    assert not error, f"Failed to create category (status {error.status}): {error.error}"
    # <<<<< CREATE CATEGORY STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            canary_requests: list[str] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))
            page.on("request", lambda request: canary_requests.append(request.url) if CANARY_HOST in request.url else None)

            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")

            for path in ("/upload", "/batch"):
                await page.goto(f"{lrr_client.lrr_base_url}{path}", timeout=60000)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_load_state("networkidle")

                assert not canary_requests, f"Canary request fired on {path}: {canary_requests}"
                for evt in console_evts:
                    assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired in console on {path}: {evt.text}"
                assert await page.locator("[onerror]").count() == 0, f"Live onerror attribute injected on {path}"
                assert await page.locator("[onload]").count() == 0, f"Live onload attribute injected on {path}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: source tag value not escaped in public/js/mod/common.js buildTagsDiv (:268 href)",
    strict=False,
)
async def test_tag_escape_reader(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — an archive's source: tag must not execute when the reader renders its tag list
    (the source-namespace URL is placed into the href attribute without escaping).

    1. Upload an archive whose source: tag breaks out of the tag-link href.
    2. Open the reader for that archive.
    3. Assert no canary request fired, no console marker logged, no live onerror/onload attribute.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    tag_payload = f'source:"><img src={CANARY_URL} onerror=console.error(\'{CANARY_MARKER}\')>'
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = create_archive_file(Path(tmpdir), "archive_1", 3)
        response, error = await upload_archive(lrr_client, save_path, save_path.name, semaphore, title="Title 1", tags=tag_payload)
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            canary_requests: list[str] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))
            page.on("request", lambda request: canary_requests.append(request.url) if CANARY_HOST in request.url else None)

            await page.goto(f"{lrr_client.lrr_base_url}/reader?id={arcid}", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            assert not canary_requests, f"Canary request fired from reader tag list: {canary_requests}"
            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired in console: {evt.text}"
            assert await page.locator("[onerror]").count() == 0, "Live onerror attribute injected from tag list"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: tag namespace/value not escaped in public/js/stats.js:34 (tag cloud)",
    strict=False,
)
async def test_tag_escape_stats(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — an archive tag must not execute when the statistics page renders the tag cloud.

    1. Upload two archives sharing an XSS payload tag (weight >= 2 so it survives the cloud's
       minweight filter), then rebuild the stat hash.
    2. Open the statistics page.
    3. Assert no canary request fired, no console marker logged, no live onerror/onload attribute.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    tag_payload = f"xss:<img src={CANARY_URL} onerror=console.error('{CANARY_MARKER}')>"
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(2):
            save_path = create_archive_file(Path(tmpdir), f"archive_{i + 1}", 3)
            response, error = await upload_archive(lrr_client, save_path, save_path.name, semaphore, title=f"Title {i + 1}", tags=tag_payload)
            assert not error, f"Upload failed (status {error.status}): {error.error}"
    await trigger_stat_rebuild(lrr_client)
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            canary_requests: list[str] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))
            page.on("request", lambda request: canary_requests.append(request.url) if CANARY_HOST in request.url else None)

            await page.goto(f"{lrr_client.lrr_base_url}/stats", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            assert not canary_requests, f"Canary request fired from stats tag cloud: {canary_requests}"
            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired in console: {evt.text}"
            assert await page.locator("[onerror]").count() == 0, "Live onerror attribute injected from tag cloud"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: tag value not escaped in the compact/datatables tag column public/js/mod/index_datatables.js:168/174 and the namespace dropdown public/js/mod/index.js:335",
    strict=False,
)
async def test_tag_escape_index_compact(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — an archive tag must not execute in the compact/DataTables tag column or the
    namespace sort dropdown on the index page.

    1. Upload an archive with an XSS payload in a tag, then rebuild the stat hash.
    2. Open the index page and switch to compact mode so the tag column renders.
    3. Assert no canary request fired, no console marker logged, no live onerror/onload attribute.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    tag_payload = f"xss:<img src={CANARY_URL} onerror=console.error('{CANARY_MARKER}')>"
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = create_archive_file(Path(tmpdir), "archive_1", 3)
        response, error = await upload_archive(lrr_client, save_path, save_path.name, semaphore, title="Title 1", tags=tag_payload)
        assert not error, f"Upload failed (status {error.status}): {error.error}"
    await trigger_stat_rebuild(lrr_client)
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            canary_requests: list[str] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))
            page.on("request", lambda request: canary_requests.append(request.url) if CANARY_HOST in request.url else None)

            await page.goto(lrr_client.lrr_base_url, timeout=60000)
            await page.wait_for_load_state("networkidle")
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
            await switch_display_mode(page, "compact")
            await page.wait_for_load_state("networkidle")

            assert not canary_requests, f"Canary request fired from index tag column/namespace dropdown: {canary_requests}"
            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired in console: {evt.text}"
            assert await page.locator(f"[onerror*='{CANARY_MARKER}']").count() == 0, "Live onerror attribute injected on compact index"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: ToC chapter name not escaped in public/js/reader.js:1680/1685 (chapter selector)",
    strict=False,
)
async def test_toc_name_escape_reader(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — a user-set ToC chapter title must not execute when the reader builds its chapter
    selector.

    1. Upload an archive, then add a ToC entry whose title is an XSS payload.
    2. Open the reader for that archive (the chapter selector is built from the ToC on load).
    3. Assert no canary request fired, no console marker logged, no live onerror/onload attribute.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD & TOC STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = create_archive_file(Path(tmpdir), "archive_1", 3)
        response, error = await upload_archive(lrr_client, save_path, save_path.name, semaphore, title="Title 1", tags="")
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid

    _, error = await lrr_client.archive_api.add_toc_entry(AddTocEntryRequest(arcid=arcid, page=1, title=MARKUP_PAYLOAD))
    assert not error, f"Failed to add ToC entry (status {error.status}): {error.error}"
    # <<<<< UPLOAD & TOC STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            canary_requests: list[str] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))
            page.on("request", lambda request: canary_requests.append(request.url) if CANARY_HOST in request.url else None)

            await page.goto(f"{lrr_client.lrr_base_url}/reader?id={arcid}", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            assert not canary_requests, f"Canary request fired from reader chapter selector: {canary_requests}"
            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired in console: {evt.text}"
            assert await page.locator("[onerror]").count() == 0, "Live onerror attribute injected from chapter selector"
            assert await page.locator("[onload]").count() == 0, "Live onload attribute injected from chapter selector"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: archive filename not HTML-escaped in the filename input value attribute (templates/edit.html.tt2:65)",
    strict=False,
)
async def test_edit_filename_escape(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — an archive filename must not break out of the filename input on the edit page.

    1. Upload an archive, then inject a quote-breakout payload directly into the archive's stored
       file field via Redis.
    2. Log in, then open /edit for that archive.
    3. Assert no console marker logged.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = create_archive_file(Path(tmpdir), "archive_1", 3)
        response, error = await upload_archive(lrr_client, save_path, "archive_1.zip", semaphore, title="Title 1", tags="")
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid

    environment.redis_client.select(0)
    environment.redis_client.hset(arcid, "file", ATTR_PAYLOAD)
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")

            await page.goto(f"{lrr_client.lrr_base_url}/edit?id={arcid}", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired from edit filename: {evt.text}"
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: archive title/filename/tags not escaped in templates/duplicates.html.tt2:103/104 (title), :112 (filename), :120 (tags in onmouseover)",
    strict=False,
)
async def test_duplicates_metadata_escape(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — archive title, filename, and tags must not execute on the duplicates page.

    1. Upload two archives with XSS payloads in title (and tags), one carrying a payload filename.
    2. Register the pair as a duplicate group directly in Redis so the page renders them.
    3. Log in, open /duplicates, and hover the tag tooltip to exercise the onmouseover tags sink.
    4. Assert no console marker logged.
    5. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    arcids: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(2):
            save_path = create_archive_file(Path(tmpdir), f"archive_{i + 1}", 3)
            response, error = await upload_archive(
                lrr_client, save_path, f"{ATTR_PAYLOAD}_{i}.zip", semaphore, title=ATTR_PAYLOAD, tags=f"xss:{ATTR_PAYLOAD}",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
            arcids.append(response.arcid)
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> DUPLICATE GROUP STAGE >>>>>
    environment.redis_client.select(2)
    environment.redis_client.hset("LRR_DUPLICATE_GROUPS", "test_group", json.dumps(arcids))
    # <<<<< DUPLICATE GROUP STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")

            await page.goto(f"{lrr_client.lrr_base_url}/duplicates", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            tooltip = page.locator(".tag-tooltip").first
            if await tooltip.count() > 0:
                await tooltip.hover()
                await page.wait_for_timeout(500)

            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired from duplicates page: {evt.text}"
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: plugin config value not HTML-escaped in the input value attribute (templates/plugins.html.tt2:199/200)",
    strict=False,
)
async def test_plugin_config_value_escape(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — a saved plugin config value must not break out of its input on the plugins page.

    1. Write an attribute-breakout payload as the regexplugin config directly in Redis.
    2. Log in, then open /config/plugins.
    3. Assert no console marker logged.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> PLUGIN CONFIG STAGE >>>>>
    customargs = []
    for _ in range(8):
        customargs.append(ATTR_PAYLOAD)
    environment.redis_client.select(2)
    environment.redis_client.hset("LRR_PLUGIN_REGEXPLUGIN", "customargs", json.dumps(customargs))
    environment.redis_client.hset("LRR_PLUGIN_REGEXPLUGIN", "enabled", "1")
    # <<<<< PLUGIN CONFIG STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")

            await page.goto(f"{lrr_client.lrr_base_url}/config/plugins", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired from plugin config value: {evt.text}"
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: archive filename not escaped in the delete toast rendered via dangerouslySetInnerHTML (public/js/mod/server.js:333, public/js/mod/common.js:557)",
    strict=False,
)
async def test_delete_toast_filename_escape(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — an archive filename must not execute in the delete-success toast.

    1. Upload an archive whose filename carries an XSS payload.
    2. Log in, open the reader, open the archive overview overlay, delete the archive, and confirm the dialog.
    3. Assert no console marker logged from the toast.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    filename_payload = f"<img src=x onerror=console.error('{CANARY_MARKER}')>.zip"
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = create_archive_file(Path(tmpdir), "archive_1", 3)
        response, error = await upload_archive(lrr_client, save_path, filename_payload, semaphore, title="Title 1", tags="")
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")

            await page.goto(f"{lrr_client.lrr_base_url}/reader?id={arcid}", timeout=60000)
            await page.wait_for_load_state("networkidle")

            # The delete button lives inside the archive overview overlay, which is hidden until opened.
            await page.locator("#toggle-archive-overlay").first.click()
            await page.locator("#delete-archive").click()
            await page.locator(".swal2-confirm").click()
            await page.wait_for_timeout(1000)

            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired from delete toast: {evt.text}"
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: download-by-URL response not escaped in the upload result row (public/js/upload.js:154, reflected url)",
    strict=False,
)
async def test_upload_url_reflect_escape(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Reflected XSS — a download-by-URL value must not execute in the upload page result row.

    1. Log in, open the upload page.
    2. Submit a download URL that is an XSS payload.
    3. Assert no console marker logged from the appended result row.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    url_payload = f"<img src=x onerror=console.error('{CANARY_MARKER}')>"
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")

            await page.goto(f"{lrr_client.lrr_base_url}/upload", timeout=60000)
            await page.wait_for_load_state("networkidle")

            await page.locator("#urlForm").fill(url_payload)
            await page.locator("#download-url").click()
            await page.locator("#files > *").first.wait_for(state="attached", timeout=20000)
            await page.wait_for_timeout(1000)

            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired from upload URL result row: {evt.text}"
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.security
@pytest.mark.xfail(
    reason="requires LRR-side fix: uploaded filename not escaped in the upload result row (public/js/upload.js:28/35/43/52)",
    strict=False,
)
async def test_upload_filename_escape(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS — an uploaded archive filename must not execute in the upload page result row.

    1. Log in, open the upload page.
    2. Upload a file whose filename is an XSS payload through the file input.
    3. Assert no console marker logged from the appended result row.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")

            await page.goto(f"{lrr_client.lrr_base_url}/upload", timeout=60000)
            await page.wait_for_load_state("networkidle")

            with tempfile.TemporaryDirectory() as tmpdir:
                save_path = create_archive_file(Path(tmpdir), "archive_1", 3)
                payload_path = Path(tmpdir) / f"<img src=x onerror=console.error('{CANARY_MARKER}')>.zip"
                save_path.rename(payload_path)
                await page.locator("#fileupload").set_input_files(str(payload_path))
                await page.locator("#files > *").first.wait_for(state="attached", timeout=20000)
                await page.wait_for_timeout(1000)

            for evt in console_evts:
                assert CANARY_MARKER not in (evt.text or ""), f"Canary marker fired from upload filename result row: {evt.text}"
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)
