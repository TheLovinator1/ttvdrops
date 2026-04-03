import re
from typing import TYPE_CHECKING

from django.template.loader import render_to_string
from django.test import RequestFactory

if TYPE_CHECKING:
    from django.core.handlers.wsgi import WSGIRequest


def _render_meta_tags(context: dict | None = None, path: str = "/test/") -> str:
    """Helper function to render meta tags template with a test request and given context.

    Args:
        context: Optional dictionary of context variables to pass to the template.
        path: The URL path for the test request.

    Returns:
        The rendered HTML content of the meta tags.
    """
    request: WSGIRequest = RequestFactory().get(path)
    return render_to_string(
        "includes/meta_tags.html",
        context=context or {},
        request=request,
    )


def _extract_meta_content(content: str, key: str) -> str:
    """Extract the content of a meta tag by its name or property.

    Args:
        content: The full HTML content to search within.
        key: The value of the name or property attribute to match.

    Returns:
        The content attribute value of the matched meta tag.
    """
    pattern: re.Pattern[str] = re.compile(
        rf'<meta\s+(?:name|property)="{re.escape(key)}"\s+content="([^"]*)"\s*/?>',
    )
    match: re.Match[str] | None = pattern.search(content)
    assert match is not None, f"Expected meta tag for {key!r} in rendered output."
    return match.group(1)


def test_meta_tags_use_request_absolute_url_for_og_url_and_canonical() -> None:
    """Test that without page_url in context, og:url and canonical tags use request.build_absolute_uri."""
    content: str = _render_meta_tags(path="/drops/")

    assert (
        _extract_meta_content(content, "og:url")
        == "https://ttvdrops.lovinator.space/drops/"
    )
    canonical_pattern: re.Pattern[str] = re.compile(
        r'<link\s+rel="canonical"\s+href="https://ttvdrops\.lovinator\.space/drops/"\s*/?>',
    )
    assert canonical_pattern.search(content) is not None


def test_meta_tags_use_explicit_page_url_for_og_url_and_canonical() -> None:
    """Test that providing page_url in context results in correct og:url and canonical tags."""
    content: str = _render_meta_tags(
        {
            "page_url": "https://ttvdrops.lovinator.space/custom-page/",
        },
        path="/ignored/",
    )

    assert (
        _extract_meta_content(content, "og:url")
        == "https://ttvdrops.lovinator.space/custom-page/"
    )
    canonical_pattern: re.Pattern[str] = re.compile(
        r'<link\s+rel="canonical"\s+href="https://ttvdrops\.lovinator\.space/custom-page/"\s*/?>',
    )
    assert canonical_pattern.search(content) is not None


def test_meta_tags_twitter_card_is_summary_without_image() -> None:
    """Test that without page_image in context, twitter:card is summary and no twitter:image tag is present."""
    content: str = _render_meta_tags()

    assert _extract_meta_content(content, "twitter:card") == "summary"
    assert 'name="twitter:image"' not in content


def test_meta_tags_twitter_card_is_summary_large_image_with_page_image() -> None:
    """Test that providing page_image in context results in twitter:card being summary_large_image and correct og:image and twitter:image tags."""
    content: str = _render_meta_tags({
        "page_image": "https://ttvdrops.lovinator.space/image.png",
        "page_image_width": 1200,
        "page_image_height": 630,
    })

    assert _extract_meta_content(content, "twitter:card") == "summary_large_image"
    assert (
        _extract_meta_content(content, "og:image")
        == "https://ttvdrops.lovinator.space/image.png"
    )
    assert (
        _extract_meta_content(content, "twitter:image")
        == "https://ttvdrops.lovinator.space/image.png"
    )
    assert _extract_meta_content(content, "og:image:width") == "1200"
    assert _extract_meta_content(content, "og:image:height") == "630"


def test_meta_tags_render_pagination_links() -> None:
    """Test that pagination_info in context results in correct prev/next link tags in output."""
    content: str = _render_meta_tags({
        "pagination_info": [
            {"rel": "prev", "url": "https://ttvdrops.lovinator.space/page/1/"},
            {"rel": "next", "url": "https://ttvdrops.lovinator.space/page/3/"},
        ],
    })

    assert (
        '<link rel="prev" href="https://ttvdrops.lovinator.space/page/1/" />' in content
    )
    assert (
        '<link rel="next" href="https://ttvdrops.lovinator.space/page/3/" />' in content
    )
