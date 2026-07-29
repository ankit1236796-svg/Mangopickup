from .common import (
    detect_site, build_scraper_url, fetch_page, HEADERS, fetch_with_502_retry,
    extract_generic_product_name,
)
from . import apple

__all__ = [
    "detect_site", "build_scraper_url", "fetch_page", "HEADERS", "fetch_with_502_retry",
    "extract_generic_product_name", "apple",
]
