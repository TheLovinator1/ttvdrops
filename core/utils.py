from typing import Any
from xml.etree.ElementTree import Element  # noqa: S405
from xml.etree.ElementTree import SubElement  # noqa: S405
from xml.etree.ElementTree import tostring  # noqa: S405

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
    urlset = Element("urlset")
    urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

    for url_entry in sitemap_urls:
        url = SubElement(urlset, "url")
        loc = url_entry.get("loc")
        if loc:
            child = SubElement(url, "loc")
            child.text = loc

    return tostring(urlset, encoding="unicode")
