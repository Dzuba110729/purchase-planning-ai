"""Static dashboard and order workbook."""

from .site import build_site
from .xlsx import build_xlsx

__all__ = ["build_site", "build_xlsx"]
