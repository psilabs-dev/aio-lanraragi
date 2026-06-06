"""
Edit page UI integration tests for the LANraragi server.
Covers the /edit page for archives and tankoubons.
"""

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

import playwright.async_api
import playwright.async_api._generated
import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.tankoubon import (
    AddArchiveToTankoubonRequest,
    CreateTankoubonRequest,
    GetTankoubonRequest,
)

from aio_lanraragi_tests.common import DEFAULT_LRR_PASSWORD
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    upload_archive,
)
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
@pytest.mark.xfail(
    reason="requires LRR-side fix: archive title must be HTML-escaped in templates/edit.html.tt2 (server render) and public/js/edit.js (Add Archive client render)",
    strict=False,
)
async def test_tank_edit_archive_title_escape(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Stored XSS regression — archive titles rendered in the tank-edit page must be HTML-escaped
    on both render paths (server-side TT2 in templates/edit.html.tt2 and client-side jQuery
    append in public/js/edit.js Edit.addArchiveToTank).

    1. Upload two archives whose titles contain XSS payloads (script tag, img onerror, svg onload,
       and an img src canary URL).
    2. Create a tankoubon and add the first archive to it via the API.
    3. Open /edit?id=TANK_* — exercises the server-render path. Assert:
       - No live <script> tag inside #tank-archive-list.
       - No element bearing an onerror or onload attribute inside #tank-archive-list.
       - No console event mentions the canary string.
       - No network request to the canary URL.
       - The literal escaped text appears in the list (sanity check that the title actually round-tripped).
    4. Use the Add Archive UI to attach the second archive — exercises the client-render path. Repeat
       the assertions above for the newly-appended row.
    5. Click Save Metadata, reload the page, and re-check the server-render assertions for the
       persisted state.
    6. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    canary_url = "//xss-canary.invalid/fired"
    canary_marker = "xss_fired_marker"
    malicious_title = (
        f"<script>console.error('{canary_marker}')</script>"
        f"<img src=\"{canary_url}\" onerror=\"console.error('{canary_marker}')\">"
        f"<svg onload=\"console.error('{canary_marker}')\"></svg>"
    )

    archive_ids: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        for i in range(2):
            save_path = create_archive_file(tmpdir, f"xss-probe-{i}", 3)
            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=malicious_title, tags="",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
            archive_ids.append(response.arcid)
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> CREATE TANKOUBON STAGE >>>>>
    response, error = await lrr_client.tankoubon_api.create_tankoubon(CreateTankoubonRequest(name="XSS Tank"))
    assert not error, f"Failed to create tankoubon (status {error.status}): {error.error}"
    tank_id = response.tank_id
    del response, error

    response, error = await lrr_client.tankoubon_api.add_archive_to_tankoubon(
        AddArchiveToTankoubonRequest(tank_id=tank_id, arcid=archive_ids[0])
    )
    assert not error, f"Failed to add archive to tankoubon (status {error.status}): {error.error}"
    del response, error
    # <<<<< CREATE TANKOUBON STAGE <<<<<

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
            page.on("request", lambda request: canary_requests.append(request.url) if "xss-canary.invalid" in request.url else None)

            # >>> SERVER-RENDER PATH (finding #7) >>>
            await page.goto(f"{lrr_client.lrr_base_url}/edit?id={tank_id}", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            # dismiss new version overlay if present
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)

            tank_list = page.locator("#tank-archive-list")
            await tank_list.wait_for(state="attached", timeout=10000)

            script_count = await tank_list.locator("script").count()
            assert script_count == 0, f"Live <script> tag found in #tank-archive-list (server render) — count={script_count}"

            onerror_count = await tank_list.locator("[onerror]").count()
            onload_count = await tank_list.locator("[onload]").count()
            assert onerror_count == 0, f"Live onerror attribute found in #tank-archive-list (server render) — count={onerror_count}"
            assert onload_count == 0, f"Live onload attribute found in #tank-archive-list (server render) — count={onload_count}"

            for evt in console_evts:
                assert canary_marker not in (evt.text or ""), f"Canary marker fired in console (server render): {evt.text}"

            assert not canary_requests, f"Canary network request fired (server render): {canary_requests}"

            # Sanity: the escaped title should appear as text in the list.
            list_text = await tank_list.inner_text()
            assert "<script>" in list_text or "&lt;script&gt;" not in list_text, (
                f"Expected escaped script literal in list text; got: {list_text!r}"
            )
            # <<< SERVER-RENDER PATH <<<

            # >>> CLIENT-RENDER PATH (finding #8) >>>
            await page.locator("#add-archive-id").fill(archive_ids[1])
            await page.locator("#add-archive-btn").click()
            await page.locator(f"#tank-archive-list li[data-id='{archive_ids[1]}']").wait_for(state="attached", timeout=10000)

            script_count = await tank_list.locator("script").count()
            assert script_count == 0, f"Live <script> tag found after Add Archive (client render) — count={script_count}"

            onerror_count = await tank_list.locator("[onerror]").count()
            onload_count = await tank_list.locator("[onload]").count()
            assert onerror_count == 0, f"Live onerror attribute found after Add Archive (client render) — count={onerror_count}"
            assert onload_count == 0, f"Live onload attribute found after Add Archive (client render) — count={onload_count}"

            for evt in console_evts:
                assert canary_marker not in (evt.text or ""), f"Canary marker fired in console (client render): {evt.text}"

            assert not canary_requests, f"Canary network request fired (client render): {canary_requests}"
            # <<< CLIENT-RENDER PATH <<<

            # >>> SAVE + RELOAD (server-render after persist) >>>
            await page.locator("#save-metadata").click()
            await page.wait_for_load_state("networkidle")
            await page.reload()
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            tank_list = page.locator("#tank-archive-list")
            await tank_list.wait_for(state="attached", timeout=10000)

            script_count = await tank_list.locator("script").count()
            assert script_count == 0, f"Live <script> tag found after save+reload — count={script_count}"

            onerror_count = await tank_list.locator("[onerror]").count()
            onload_count = await tank_list.locator("[onload]").count()
            assert onerror_count == 0, f"Live onerror attribute found after save+reload — count={onerror_count}"
            assert onload_count == 0, f"Live onload attribute found after save+reload — count={onload_count}"

            for evt in console_evts:
                assert canary_marker not in (evt.text or ""), f"Canary marker fired in console (after save+reload): {evt.text}"

            assert not canary_requests, f"Canary network request fired (after save+reload): {canary_requests}"
            # <<< SAVE + RELOAD <<<

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
async def test_tankoubon_edit_page_save(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Test that the tank metadata edit page saves the name via the UI.

    1. Upload 3 archives, create a tank, add archives to it.
    2. Open /edit?id=TANK_xxx in browser, wait for form.
    3. Change the title input, click Save Metadata.
    4. Capture the PUT /api/tankoubons/{id} response: expect 200.
    5. Re-fetch the tank via API, assert new name persisted.
    6. Expect no HTTP errors, no console errors, no server error logs.
    """

    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        for i in range(3):
            save_path = create_archive_file(tmpdir, f"tank-edit-archive-{i}", 3)
            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=f"Tank Edit Archive {i}", tags="",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"

    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives: {error.error}"
    archive_ids = [arc.arcid for arc in response.data]
    del response, error

    response, error = await lrr_client.tankoubon_api.create_tankoubon(CreateTankoubonRequest(name="Tank Edit Test"))
    assert not error, f"Failed to create tank: {error.error}"
    tank_id = response.tank_id
    del response, error

    for arc_id in archive_ids:
        response, error = await lrr_client.tankoubon_api.add_archive_to_tankoubon(
            AddArchiveToTankoubonRequest(tank_id=tank_id, arcid=arc_id)
        )
        assert not error, f"Failed to add archive to tank: {error.error}"
        del response, error

    new_name = "Tank Edit Test Renamed"
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # log in (edit page requires session auth since enable_pass defaults to 1)
            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.wait_for_load_state("networkidle")
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")

            await page.goto(f"{lrr_client.lrr_base_url}/edit?id={tank_id}", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            # dismiss new version overlay if present
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)

            put_future: asyncio.Future = asyncio.get_event_loop().create_future()
            async def on_put(response: playwright.async_api._generated.Response) -> None:
                if put_future.done():
                    return
                if response.request.method == "PUT" and f"/api/tankoubons/{tank_id}" in response.url:
                    put_future.set_result(response)
            page.on("response", on_put)

            await page.locator("#title").fill(new_name)
            await page.locator("#save-metadata").click()

            put_response = await asyncio.wait_for(put_future, timeout=10)
            assert put_response.status == 200, f"Tank PUT returned {put_response.status}: {await put_response.text()}"

            await page.wait_for_load_state("networkidle")
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()

    response, error = await lrr_client.tankoubon_api.get_tankoubon(GetTankoubonRequest(tank_id=tank_id))
    assert not error, f"Failed to get tank: {error.error}"
    assert response.name == new_name, f"Tank name not updated: got {response.name!r}, expected {new_name!r}"

    expect_no_error_logs(environment, LOGGER)
