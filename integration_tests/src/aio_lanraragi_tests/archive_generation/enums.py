
import enum


class ArchivalStrategyEnum(enum.Enum):
    NO_ARCHIVE = 0
    ZIP = 1
    RAR = 2
    TAR_GZ = 3
    LZMA = 4
    S7Z = 5
    XZ = 6
    PDF = 7
    EPUB = 8