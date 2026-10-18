"""
Ougi (default registry) designation API integration tests.
"""

import logging

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.misc import (
    CreateRegistryRequest,
)

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_ougi_lifecycle(
    environment: AbstractLRRDeploymentContext,
    lrr_client: LRRClient,
):
    """
    Test the Ougi designation across set/get/clear and auto-clear on registry delete.

    1. Get Ougi when unset, expect empty string.
    2. DELETE when unset, expect empty string returned.
    3. Set Ougi to wrong-length id, expect 400 (OpenAPI path-length validation).
    4. Set Ougi to right-length but non-REG_ id, expect 400 (model regex validation).
    5. Set Ougi to well-formed but nonexistent id, expect 404.
    6. Create a local registry, set as Ougi, get reflects it.
    7. Explicit DELETE returns the previous id and clears the designation.
    8. Re-set Ougi, then DELETE the underlying registry; Ougi auto-clears.
    """
    environment.setup(with_api_key=True)

    # >>>>> GET WHEN UNSET >>>>>
    response, error = await lrr_client.misc_api.get_ougi()
    assert not error, f"Failed to get Ougi (status {error.status}): {error.error}"
    assert response.id == "", f"Expected empty string when unset, got: {response.id!r}"
    # <<<<< GET WHEN UNSET <<<<<

    # >>>>> DELETE WHEN UNSET >>>>>
    response, error = await lrr_client.misc_api.remove_ougi()
    assert not error, f"Failed to clear unset Ougi (status {error.status}): {error.error}"
    assert response.id == "", f"Expected empty string when no Ougi was set, got: {response.id!r}"
    # <<<<< DELETE WHEN UNSET <<<<<

    # >>>>> SET WRONG-LENGTH ID >>>>>
    response, error = await lrr_client.misc_api.update_ougi("not-a-reg-id")
    assert error is not None, "Expected error for wrong-length registry id"
    assert error.status == 400, f"Expected 400 for wrong-length id, got {error.status}"
    # <<<<< SET WRONG-LENGTH ID <<<<<

    # >>>>> SET RIGHT-LENGTH NON-REG ID >>>>>
    response, error = await lrr_client.misc_api.update_ougi("ABCDEFGHIJKLMN")
    assert error is not None, "Expected error for right-length non-REG_ registry id"
    assert error.status == 400, f"Expected 400 for non-REG_ id, got {error.status}"
    # <<<<< SET RIGHT-LENGTH NON-REG ID <<<<<

    # >>>>> SET NONEXISTENT ID >>>>>
    response, error = await lrr_client.misc_api.update_ougi("REG_0000000001")
    assert error is not None, "Expected error for nonexistent registry id"
    assert error.status == 404, f"Expected 404 for nonexistent id, got {error.status}"

    response, error = await lrr_client.misc_api.get_ougi()
    assert not error, f"Failed to get Ougi (status {error.status}): {error.error}"
    assert response.id == "", f"Ougi must remain unset after failed PUT, got: {response.id!r}"
    # <<<<< SET NONEXISTENT ID <<<<<

    # >>>>> SET VALID ID >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="ougi-test", provider="local", path=environment.local_registry_path)
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.update_ougi(reg_id)
    assert not error, f"Failed to set Ougi (status {error.status}): {error.error}"
    assert response.id == reg_id, f"Expected Ougi {reg_id}, got: {response.id}"

    response, error = await lrr_client.misc_api.get_ougi()
    assert not error, f"Failed to get Ougi (status {error.status}): {error.error}"
    assert response.id == reg_id, f"Expected Ougi {reg_id}, got: {response.id}"
    # <<<<< SET VALID ID <<<<<

    # >>>>> EXPLICIT DELETE >>>>>
    response, error = await lrr_client.misc_api.remove_ougi()
    assert not error, f"Failed to clear Ougi (status {error.status}): {error.error}"
    assert response.id == reg_id, f"Expected previous id {reg_id}, got: {response.id}"

    response, error = await lrr_client.misc_api.get_ougi()
    assert not error, f"Failed to get Ougi (status {error.status}): {error.error}"
    assert response.id == "", f"Expected empty string after clear, got: {response.id!r}"
    # <<<<< EXPLICIT DELETE <<<<<

    # >>>>> AUTO-CLEAR ON REGISTRY DELETE >>>>>
    response, error = await lrr_client.misc_api.update_ougi(reg_id)
    assert not error, f"Failed to re-set Ougi (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_ougi()
    assert not error, f"Failed to get Ougi (status {error.status}): {error.error}"
    assert response.id == "", (
        f"Ougi must auto-clear when its registry is deleted, got: {response.id!r}"
    )
    # <<<<< AUTO-CLEAR ON REGISTRY DELETE <<<<<

    expect_no_error_logs(environment, LOGGER)
