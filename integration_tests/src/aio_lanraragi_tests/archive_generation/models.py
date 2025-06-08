from pathlib import Path
from PIL import Image

from typing import List, Tuple, Union

from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum

LIGHT_GRAY = (200, 200, 200)

class Page:

    width: int
    height: int
    left_boundary: int
    right_boundary: int
    upper_boundary: int
    lower_boundary: int
    margin: int
    font_size: int
    image: Image.Image
    first_n_bytes = int
    image_format: str
    text: str
    filename: str

    def close(self):
        if isinstance(self.image, Image.Image):
            self.image.close()
        self.image = None

class CreatePageRequest:
    """
    Request to create a Page object, which contains an Image object
    in memory.
    """

    def __init__(
            self, width: int, height: int, filename: str,
            background_color: Union[str, Tuple[int, int, int]]=LIGHT_GRAY,
            first_n_bytes: int=None,
            image_format: str='PNG',
            text: str=None
    ):
        self.width = width
        self.height = height
        self.filename = filename
        self.background_color = background_color
        self.first_n_bytes = first_n_bytes
        self.image_format = image_format
        self.text = text

class CreatePageResponseStatus:
    SUCCESS = 0
    FAILURE = 1

class CreatePageResponse:
    """
    Response from passing a CreatePageRequest object to create_page.
    """
    page: Page
    status: CreatePageResponseStatus
    error: str

class WriteArchiveRequest:
    """
    Request to write to an archive on disk based on a sequence of create
    page requests.
    """

    def __init__(
        self, create_page_requests: List[CreatePageRequest],
        save_path: Path,
        archival_strategy: ArchivalStrategyEnum=ArchivalStrategyEnum.ZIP,
    ):
        self.create_page_requests = create_page_requests
        self.save_path = save_path
        self.archival_strategy = archival_strategy

class WriteArchiveResponseStatus:
    SUCCESS = 0
    FAILURE = 1

class WriteArchiveResponse:
    status: WriteArchiveResponseStatus
    error: str
    save_path: Path

class TagGenerator:
    """
    Tag generation assignment logic. Assumption of IID between different tags, although
    this is not often true in the real world it will be sufficient for data generation purposes.

    assignment_probability: probability an archive will be assigned this tag.
    """

    def __init__(self, tag_name: str, assign_probability: float):
        if not tag_name:
            raise ValueError("Missing tag name!")
        if not isinstance(tag_name, str):
            raise TypeError(f"Incorrect type for tag name: {type(tag_name)}!")
        if assign_probability < 0 or assign_probability > 1:
            raise ValueError(f"Invalid range for assign probability: {assign_probability}")

        self.tag_name = tag_name
        self.assign_probability = assign_probability
        pass
    
    def __repr__(self):
        return str(self.__dict__)