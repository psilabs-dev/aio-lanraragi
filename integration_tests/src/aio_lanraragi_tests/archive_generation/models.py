import enum
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict

from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum

LIGHT_GRAY = (200, 200, 200)

class CreatePageResponseStatus(enum.Enum):
    SUCCESS = 'success'
    FAILURE = 'failure'

class Page(BaseModel):

    width: int
    height: int
    left_boundary: int
    right_boundary: int
    upper_boundary: int
    lower_boundary: int
    margin: int
    font_size: int
    image: Image.Image | None = None
    first_n_bytes: int | None = None
    image_format: str
    text: str
    filename: str

    model_config = ConfigDict(arbitrary_types_allowed=True)

class CreatePageRequest(BaseModel):
    """
    Request to create a Page object, which contains an Image object
    in memory.
    """

    width: int
    height: int
    filename: str
    background_color: str | tuple[int, int, int] = LIGHT_GRAY
    first_n_bytes: int | None = None
    image_format: str = 'PNG'
    text: str | None = None

class CreatePageResponse(BaseModel):
    """
    Response from passing a CreatePageRequest object to create_page.
    """
    page: Page | None = None
    status: CreatePageResponseStatus
    error: str | None = None

class WriteArchiveRequest(BaseModel):
    """
    Request to write to an archive on disk based on a sequence of create
    page requests.
    """

    create_page_requests: list[CreatePageRequest]
    save_path: Path
    archival_strategy: ArchivalStrategyEnum

class WriteArchiveResponseStatus(enum.Enum):
    SUCCESS = 'success'
    FAILURE = 'failure'

class WriteArchiveResponse(BaseModel):
    status: WriteArchiveResponseStatus
    error: str | None = None
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
