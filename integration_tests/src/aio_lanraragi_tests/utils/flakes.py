import asyncio
import logging
import numpy as np
from pathlib import Path
import tempfile
from typing import Optional

from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext, expect_no_error_logs
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    save_archives,
    upload_archive,
    upload_archives
)

LOGGER = logging.getLogger(__name__)

async def xfail_catch_flakes_inner(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
    num_archives: int = 10,
    npgenerator: Optional[np.random.Generator] = None,
) -> None:
    """
    Inner implementation for xfail flake-catching test cases.

    THIS FUNCTION EXISTS SOLELY TO BE CALLED BY TEST CASES.

    On Windows test environments (particularly in CI), the first test case in a module
    that performs concurrent archive uploads often fails due to network flakes such as:
    - Remote host connection closures
    - Connection refused errors
    - High client request pressure overwhelming an unprepared host

    This function "warms up" the Windows test host by performing a lightweight archive
    upload operation before the actual test cases run. The calling test case should be
    decorated with:
        @pytest.mark.skipif(sys.platform != "win32", reason="Cache priming required only for flaky Windows testing environments.")
        @pytest.mark.asyncio
        @pytest.mark.xfail

    The xfail marker ensures that occasional failures in this warmup test are expected
    and ignored, while still providing the cache-priming benefit for subsequent tests.

    Args:
        lrr_client:     The LANraragi client instance.
        semaphore:      Semaphore for controlling concurrent operations.
        environment:    The LRR deployment context.
        num_archives:   Number of archives to upload for warmup (default: 10 for light warmup,
                        use 100 for heavy warmup in tests with bulk uploads).
        npgenerator:    Optional numpy random generator. If provided, uses save_archives() +
                        upload_archives() for random archive generation. If None, uses
                        create_archive_file() + upload_archive() for deterministic generation.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    LOGGER.debug("Established connection with test LRR server.")
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    assert not any(environment.archives_dir.iterdir()), "Archive directory is not empty!"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {num_archives} archives to upload for warmup.")

        if npgenerator is not None:
            # Heavy warmup: use random archive generation
            write_responses = save_archives(num_archives, tmpdir, npgenerator)
            assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"
            await upload_archives(write_responses, npgenerator, semaphore, lrr_client)
        else:
            # Light warmup: use deterministic archive generation
            archive_specs = [
                {"name": f"warmup_{i}", "title": f"Warmup Archive {i}", "tags": "warmup:test", "pages": 5}
                for i in range(num_archives)
            ]
            for spec in archive_specs:
                save_path = create_archive_file(tmpdir, spec["name"], spec["pages"])
                await upload_archive(
                    lrr_client, save_path, save_path.name, semaphore,
                    title=spec["title"], tags=spec["tags"]
                )
    # <<<<< UPLOAD STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)
