"""
Download an archive by its arcid to a file.
File path is determined by filename and extension, obtained by calling the metadata API.
"""

import argparse
import asyncio
from lanraragi import LRRClient
from lanraragi.models.archive import DownloadArchiveRequest, GetArchiveMetadataRequest

async def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--arcid", type=str, required=True)
    parser.add_argument("--address", type=str, default="http://localhost:3000")
    parser.add_argument("--api-key", type=str, default="lanraragi")
    args = parser.parse_args()
    arcid: str = args.arcid
    address: str = args.address
    api_key: str = args.api_key

    async with LRRClient(address, lrr_api_key=api_key) as lrr:

        # get archive metadata
        response, err = await lrr.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
        if err:
            raise Exception(f"Encountered error while getting archive metadata: {err.error}")
        filename: str = response.filename
        extension: str = response.extension

        # download archive
        response, err = await lrr.archive_api.download_archive(DownloadArchiveRequest(arcid=arcid))
        if err:
            raise Exception(f"Encountered error while downloading archive: {err.error}")
        with open(f"{filename}.{extension}", "wb") as f:  # noqa: ASYNC230
            f.write(response.data)

        print(f"Downloaded archive to {filename}.{extension}")

if __name__ == "__main__":
    asyncio.run(main())