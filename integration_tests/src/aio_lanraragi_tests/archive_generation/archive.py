

import logging
import multiprocessing
from pathlib import Path
import tarfile
import tempfile
from typing import List, Union

import zipfile
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.models import CreatePageRequest, WriteArchiveRequest, WriteArchiveResponse, WriteArchiveResponseStatus
from aio_lanraragi_tests.archive_generation.page import save_page_to_dir

logger = logging.getLogger("manycbz")

def write_archive_to_disk(request: WriteArchiveRequest) -> WriteArchiveResponse:
    """
    Writes an archive to disk from a request.
    """
    response = WriteArchiveResponse()
    create_page_requests = request.create_page_requests
    save_path = request.save_path
    strategy = request.archival_strategy

    if not create_page_requests:
        raise ValueError("Invalid archive! No pages found.")
    if not save_path:
        raise ValueError("No save path!")
    if not strategy:
        raise ValueError("No archival strategy!")

    if strategy == ArchivalStrategyEnum.NO_ARCHIVE:
        try:
            save_path.mkdir(parents=True, exist_ok=True)
            for create_page_request in create_page_requests:
                save_page_to_dir(create_page_request, save_path)
            response.status = WriteArchiveResponseStatus.SUCCESS
            response.save_path = save_path
            return response
        except Exception as e:
            response.status = WriteArchiveResponseStatus.FAILURE
            response.error = str(e)
            return response

    # All other strategies involve creating a temp directory, creating images in that tempdir,
    # then moving these images into the appropriate compressed file.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_save_dir = Path(tmpdir)
        request.archival_strategy = ArchivalStrategyEnum.NO_ARCHIVE
        request.save_path = tmp_save_dir
        response = write_archive_to_disk(request)
        if response.status == WriteArchiveResponseStatus.FAILURE:
            logger.error(f"Failed to write pages to disk due to error: {response.error}")
            return response

        response.save_path = save_path
        if strategy == ArchivalStrategyEnum.ZIP:
            with zipfile.ZipFile(save_path, mode='w', compression=zipfile.ZIP_DEFLATED) as zipobj:
                for path in tmp_save_dir.iterdir():
                    filename = path.name
                    zipobj.write(path, filename)
            response.status = WriteArchiveResponseStatus.SUCCESS
            return response
        elif strategy == ArchivalStrategyEnum.TAR_GZ:
            with tarfile.open(save_path, mode='w:gz') as tarobj:
                for path in tmp_save_dir.iterdir():
                    tarobj.add(path, arcname=path.name)
            response.status = WriteArchiveResponseStatus.SUCCESS
            return response
        elif strategy == ArchivalStrategyEnum.XZ:
            with tarfile.open(save_path, mode='w:xz') as tarobj:
                for path in tmp_save_dir.iterdir():
                    tarobj.add(path, arcname=path.name)
            response.status = WriteArchiveResponseStatus.SUCCESS
            return response
        else:
            raise NotImplementedError(f"The compression strategy is not implemented: {strategy.name}")

def write_archives_to_disk(write_requests: List[WriteArchiveRequest]) -> List[WriteArchiveResponse]:
    """
    Write multiple archives to disk and return their responses with multiprocessing.
    """
    cpu_count = multiprocessing.cpu_count()
    pool = multiprocessing.Pool(processes=cpu_count)
    responses = pool.starmap(write_archive_to_disk, [(request,) for request in write_requests])
    pool.close()
    return responses

def create_comic(output: Union[str, Path], comic_id: str, width: int, height: int, num_pages: int, archival_strategy: ArchivalStrategyEnum=ArchivalStrategyEnum.ZIP) -> WriteArchiveResponse:
    """
    Create comic pages in a specified output directory with given metadata,
    and returns the list of paths of the images.
    """
    if isinstance(output, str):
        output = Path(output)
    
    create_page_requests = []
    for page_id in range(num_pages):
        page_name = f"pg-{str(page_id+1).zfill(len(str(num_pages)))}"
        filename = f"{page_name}.png"
        text = f"{comic_id}-{page_name}"
        create_request = CreatePageRequest(width, height, filename, text=text)
        create_page_requests.append(create_request)

    request = WriteArchiveRequest(create_page_requests, output, archival_strategy)
    response = write_archive_to_disk(request)
    return response