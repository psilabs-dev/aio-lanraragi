import io
import logging
from pathlib import Path
from typing import overload

from PIL import Image, ImageDraw, ImageFont

from aio_lanraragi_tests.archive_generation.models import (
    LIGHT_GRAY,
    CreatePageRequest,
    CreatePageResponse,
    CreatePageResponseStatus,
    Page,
)
from aio_lanraragi_tests.archive_generation.utils import get_roberta_regular_font

LOGGER = logging.getLogger(__name__)

def close_page(page: Page):
    if isinstance(page.image, Image.Image):
        page.image.close()
    page.image = None

def create_page(request: CreatePageRequest) -> CreatePageResponse:
    """
    Create a page based on a request, returns a page and an image object.
    """
    try:
        width = request.width
        height = request.height
        background_color = request.background_color
        text = request.text
        filename = request.filename
        first_n_bytes = request.first_n_bytes
        image_format = request.image_format

        if not background_color:
            background_color = LIGHT_GRAY

        margin = int(min(width, height) * 0.05)
        page = Page(
            width=request.width,
            height=request.height,
            left_boundary=margin,
            right_boundary=width-margin,
            upper_boundary=margin,
            lower_boundary=height-margin,
            margin=margin,
            font_size=int(margin*0.7),
            first_n_bytes=first_n_bytes,
            image_format=image_format,
            text=text,
            filename=filename,
            image=Image.new("RGBA", (request.width, request.height), background_color)
        )

        # make it kind of look like a comic page.
        __add_white_panel_to_page(page)
        __add_panel_boundary_to_page(page)
        __write_text_to_page(page)
        return CreatePageResponse(page=page, status=CreatePageResponseStatus.SUCCESS)
    except Exception as e:
        LOGGER.exception(f"Failed to create page from request: {str(request.__dict__)}")
        return CreatePageResponse(page=None, status=CreatePageResponseStatus.FAILURE, error=str(e))

@overload
def save_page_to_dir(page: Page, save_dir: Path, close: bool=True):
    ...

@overload
def save_page_to_dir(request: CreatePageRequest, save_dir: Path, close: bool=True):
    ...

def save_page_to_dir(page: Page | CreatePageRequest, save_dir: Path, close: bool=True):
    """
    Saves a page to a directory by combining the page filename with the save_dir parameter.
    If page is a request, creates the page first.

    If first_n_bytes is not None, save only the first bytes given by this attribute.
    """
    if isinstance(page, CreatePageRequest):
        page = create_page(page).page
        return save_page_to_dir(page, save_dir)
    if not save_dir.is_dir():
        raise NotADirectoryError(f"Cannot save page to a non-directory! {save_dir}")

    filename = page.filename
    if not filename:
        raise ValueError("Page filename cannot be empty!")
    save_path = save_dir / filename

    if not page.first_n_bytes:
        return page.image.save(save_path, format=page.image_format)

    if not isinstance(page.first_n_bytes, int):
        raise TypeError(f"Invalid data type: {type(page.first_n_bytes)}")
    if page.first_n_bytes < 1:
        raise TypeError(f"First n bytes {page.first_n_bytes} cannot be non-positive.")
    byte_array = io.BytesIO()
    page.image.save(byte_array, format=page.image_format)
    byte_array.seek(0)
    data = byte_array.read(page.first_n_bytes)
    with open(save_path, 'wb') as writer:
        writer.write(data)

    if close:
        close_page(page)
    return

def __add_panel_boundary_to_page(page: Page):
    """
    Draw panel boundaries that are the specified margin away from the border.
    """
    draw = ImageDraw.Draw(page.image)
    draw.line([(page.left_boundary, page.upper_boundary), (page.left_boundary, page.lower_boundary)], fill='black', width=1)
    draw.line([(page.left_boundary, page.upper_boundary), (page.right_boundary, page.upper_boundary)], fill='black', width=1)
    draw.line([(page.right_boundary, page.upper_boundary), (page.right_boundary, page.lower_boundary)], fill='black', width=1)
    draw.line([(page.left_boundary, page.lower_boundary), (page.right_boundary, page.lower_boundary)], fill='black', width=1)

def __add_white_panel_to_page(page: Page):
    """
    Make panel white according to boundaries.
    """
    draw = ImageDraw.Draw(page.image)
    draw.polygon([
        (page.left_boundary, page.upper_boundary), (page.right_boundary, page.upper_boundary),
        (page.right_boundary, page.lower_boundary), (page.left_boundary, page.lower_boundary)
    ], fill='white')

def __write_text_to_page(page: Page):
    """
    Write a line of text from bottom right boundary corner.
    """
    if not page.text:
        return
    font = ImageFont.truetype(get_roberta_regular_font(), size=page.font_size)
    draw = ImageDraw.Draw(page.image)
    draw.text((page.right_boundary - 10, page.lower_boundary - 10), page.text, fill='black', anchor="rb", font=font)
