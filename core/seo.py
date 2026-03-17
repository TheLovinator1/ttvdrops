from typing import Any
from typing import TypedDict


class SeoMeta(TypedDict, total=False):
    """Shared typed optional SEO metadata for template context generation."""

    page_url: str | None
    page_image: str | None
    page_image_width: int | None
    page_image_height: int | None
    og_type: str
    schema_data: dict[str, Any] | None
    breadcrumb_schema: dict[str, Any] | None
    pagination_info: list[dict[str, str]] | None
    published_date: str | None
    modified_date: str | None
    robots_directive: str
