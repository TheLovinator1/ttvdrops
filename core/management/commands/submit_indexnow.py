from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from index_now import IndexNowAuthentication
from index_now import submit_urls_to_index_now
from sitemap_parser import SiteMapParser

if TYPE_CHECKING:
    from argparse import ArgumentParser

    from sitemap_parser import SitemapIndex
    from sitemap_parser import UrlSet


def get_urls_from_sitemap(sitemap_url: str, list_of_urls: list[str]) -> list[str]:
    """Recursively parse a sitemap URL and extract all URLs.

    Args:
        sitemap_url: The URL of the sitemap to parse.
        list_of_urls: A list to accumulate the extracted URLs.

    Returns:
        A list of URLs extracted from the sitemap and any nested sitemaps.
    """
    parser = SiteMapParser(source=sitemap_url)

    if parser.has_sitemaps():
        sitemaps: SitemapIndex = parser.get_sitemaps()
        for sitemap in sitemaps:
            list_of_urls.extend(
                get_urls_from_sitemap(
                    sitemap_url=sitemap.loc,
                    list_of_urls=list_of_urls,
                ),
            )

    elif parser.has_urls():
        urls: UrlSet = parser.get_urls()
        list_of_urls.extend(url.loc for url in urls)

    return list_of_urls


def get_chucked_urls(urls: list[str], chunk_size: int = 9999) -> list[list[str]]:
    """Split a list of URLs into smaller chunks.

    Args:
        urls: The list of URLs to split.
        chunk_size: The maximum number of URLs in each chunk.

    Returns:
        A list of URL chunks, where each chunk is a list of URLs.
    """
    return [urls[i : i + chunk_size] for i in range(0, len(urls), chunk_size)]


class Command(BaseCommand):
    """Submit sitemap URLs to the IndexNow API.

    This command is useful when the site sitemap has changed and you want to
    request that search engines re-index the updated URLs.
    """

    help = "Submit sitemap to the IndexNow API."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--api-key-location",
            default="",
            help="URL where the IndexNow API key file can be downloaded.",
        )

    def handle(self, **options: str | bool) -> None:
        """Execute the command.

        Raises:
            CommandError: When the submission to IndexNow fails.
        """
        api_key_location: str = str(options["api_key_location"])
        sitemap: str = "https://ttvdrops.lovinator.space/sitemap.xml"

        # https://lovinator.space/{api_key}.txt
        api_key: str = (
            api_key_location.removesuffix("/").rsplit("/", 1)[-1].removesuffix(".txt")
        )
        if not api_key:
            msg = "API key could not be extracted from the provided URL."
            raise CommandError(msg)

        self.stdout.write(f"Submitting sitemap to IndexNow with API key: {api_key}")

        auth = IndexNowAuthentication(
            host="ttvdrops.lovinator.space",
            api_key=api_key,
            api_key_location=api_key_location,
        )

        urls: list[str] = get_urls_from_sitemap(sitemap_url=sitemap, list_of_urls=[])
        chucked_urls: list[list[str]] = get_chucked_urls(urls=urls)
        for chunk in chucked_urls:
            self.stdout.write(f"Submitting chunk of {len(chunk)} URLs to IndexNow...")
            try:
                status_code: int = submit_urls_to_index_now(
                    authentication=auth,
                    urls=chunk,
                )
            except Exception as exc:
                msg: str = f"Error submitting sitemap(s) to IndexNow: {exc}"
                raise CommandError(msg) from exc

            self.stdout.write(self.style.SUCCESS(f"IndexNow response: {status_code}"))

        self.stdout.write(self.style.SUCCESS(f"IndexNow response: {status_code}"))
