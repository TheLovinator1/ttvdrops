from typing import Any
from xml.etree.ElementTree import Element  # ruff:ignore[suspicious-xml-etree-import]
from xml.etree.ElementTree import SubElement  # ruff:ignore[suspicious-xml-etree-import]
from xml.etree.ElementTree import tostring  # ruff:ignore[suspicious-xml-etree-import]

from django.conf import settings


def _build_base_url() -> str:
    """Build the base URL for the application.

    Returns:
        str: The base URL as configured in settings.BASE_URL.
    """
    return settings.BASE_URL


def _render_urlset_xml(sitemap_urls: list[dict[str, Any]]) -> str:
    """Render a URL set as XML.

    Args:
        sitemap_urls: List of sitemap URL entry dictionaries.

    Returns:
        str: Serialized XML for a <urlset> containing the provided URLs.
    """
    urlset: Element[str] = Element("urlset")
    urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

    for url_entry in sitemap_urls:
        url: Element[str] = SubElement(urlset, "url")
        loc: str | None = url_entry.get("loc")
        if loc:
            child: Element[str] = SubElement(url, "loc")
            child.text = loc

    return tostring(urlset, encoding="unicode")
