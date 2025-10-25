
from typing import List, Optional
from pydantic import BaseModel

class S1ArchiveInfo(BaseModel):
    preid: int # ID before creating the archive (to keep track of it).
    title: str
    tags: str
    pages: int

class S1CategoryInfo(BaseModel):
    preid: int # ID before creating category
    name: str
    filter: Optional[str] # for dynamic category
    archives: Optional[List[str]] # List of archive preids (static)
