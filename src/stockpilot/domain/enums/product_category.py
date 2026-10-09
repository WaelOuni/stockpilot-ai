from enum import Enum


class ProductCategory(str, Enum):
    DRILL = "DRILL"
    SAW = "SAW"
    SANDER = "SANDER"
    SCREWDRIVER = "SCREWDRIVER"
    OTHER = "OTHER"
