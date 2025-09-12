import hashlib
import socket
import io
from pathlib import Path
from typing import List, Optional, overload, Union

__JPG_SIGNATURES = [
    "FF D8 FF E0 00 10 4A 46 49 46 00 01",
    "FF D8 FF EE",
    "FF D8 FF E1 ?? ?? 45 78 69 66 00 00"
]

__PNG_SIGNATURES = [
    "89 50 4E 47 0D 0A 1A 0A",
]

__ZIP_SIGNATURES = [
    # zip, cbz
    "50 4b 03 04",
    "50 4b 05 06",
    "50 4b 07 08",
]

__RAR_SIGNATURES = [
    # rar, cbr
    "52 61 72 21 1A 07 00",
    "52 61 72 21 1A 07 01 00",
]

__ALLOWED_SIGNATURES = __ZIP_SIGNATURES + __RAR_SIGNATURES + [
    # tar.gz file
    "1F 8B",
    # lzma
    "FD 37 7A 58 5A 00",
    # 7z
    "37 7A BC AF 27 1C",
    # xz
    "FD 37 7A 58 5A 00",
    # pdf
    "25 50 44 46 2D",
]
__IMAGE_SIGNATURES = __JPG_SIGNATURES + __PNG_SIGNATURES

ALLOWED_SIGNATURES = [signature.replace(' ', '').lower() for signature in __ALLOWED_SIGNATURES]
IMAGE_SIGNATURES = [signature.replace(' ', '').lower() for signature in __IMAGE_SIGNATURES]

ALLOWED_LRR_EXTENSIONS = {"zip", "rar", "targz", "lzma", "7z", "xz", "cbz", "cbr", "pdf"}
NULL_ARCHIVE_ID = "da39a3ee5e6b4b0d3255bfef95601890afd80709"

DEFAULT_LRR_PORT = 3000
DEFAULT_REDIS_PORT = 6379

DEFAULT_API_KEY = "lanraragi"

@overload
def compute_upload_checksum(br: io.IOBase) -> str:
    ...

@overload
def compute_upload_checksum(file_path: Path) -> str:
    ...

@overload
def compute_upload_checksum(file_path: str) -> str:
    ...

def compute_upload_checksum(file: Optional[Union[io.IOBase, Path, str]]) -> str:
    """
    Compute the SHA1 hash of an Archive before an upload for in-transit integrity checks.
    """
    sha1 = hashlib.sha1()
    if isinstance(file, io.IOBase):
        while chunk := file.read(8192):
            sha1.update(chunk)
        return sha1.hexdigest()
    elif isinstance(file, (Path, str)):
        with open(file, 'rb') as file_br:
            while chunk := file_br.read(8192):
                sha1.update(chunk)
            return sha1.hexdigest()
    else:
        raise TypeError(f"Unsupported file type {type(file)}")


@overload
def compute_archive_id(file_path: str) -> str:
    ...

@overload
def compute_archive_id(file_path: Path) -> str:
    ...

def compute_archive_id(file_path: Optional[Union[Path, str]]) -> str:
    """
    Compute the ID of a file in the same way as the server.
    """
    if isinstance(file_path, (Path, str)):
        with open(file_path, 'rb') as fb:
            data = fb.read(512000)
        
        sha1 = hashlib.sha1()
        sha1.update(data)
        digest = sha1.hexdigest()
        if digest == NULL_ARCHIVE_ID:
            raise ValueError("Computed ID is for a null value, invalid source file.")
        return digest
    else:
        raise TypeError(f"Unsupported type: {type(file_path)}")


def get_source_from_tags(tags: str) -> Union[str, None]:
    """
    Return the source from tags if exists, else None.
    """
    tags = tags.split(',')
    for tag in tags:
        if tag.startswith("source:"):
            return tag[7:]
    return None


@overload
def get_signature_hex(archive_path: str) -> str:
    ...

@overload
def get_signature_hex(archive_path: Path) -> str:
    ...

def get_signature_hex(archive_path: Optional[Union[Path, str]]) -> str:
    """
    Get first 8 bytes of archive in hex repr.
    """
    if isinstance(archive_path, (str, Path)):
        with open(archive_path, 'rb') as fb:
            signature = fb.read(24).hex()
            return signature
    else:
        raise TypeError(f"Unsupported file type: {type(archive_path)}")

def is_valid_signature_hex(signature: str, allowed_signatures: List[str]=ALLOWED_SIGNATURES) -> bool:
    """
    Check if the hex signature corresponds to a file type supported by LANraragi.
    """
    is_allowed_mime = False
    for allowed_signature in allowed_signatures:
        if signature.startswith(allowed_signature):
            is_allowed_mime = True
    return is_allowed_mime

def is_port_available(port: int):
    """
    Checks to see if the port on localhost is available.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
