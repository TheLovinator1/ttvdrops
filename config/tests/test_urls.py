import importlib
from typing import TYPE_CHECKING

from django.test.utils import override_settings
from django.urls import reverse

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import ModuleType


def _pattern_strings(module: ModuleType) -> Iterable[str]:
    """Return string representations of the URL patterns' route/regex."""
    return (str(p.pattern) for p in module.urlpatterns)


def _reload_urls_with(**overrides) -> ModuleType:
    """Reload `config.urls` while temporarily overriding Django settings.

    Returns:
        ModuleType: The `config.urls` module as imported under the
            overridden settings. The real `config.urls` module is reloaded
            back to the test-default settings before this function returns.
    """
    # Import under overridden settings
    with override_settings(**overrides):
        mod = importlib.reload(importlib.import_module("config.urls"))

    # Restore to the normal test settings to avoid leaking changes to other tests
    importlib.reload(importlib.import_module("config.urls"))
    return mod


def test_top_level_named_routes_available() -> None:
    """Top-level routes defined in `config.urls` are reversible."""
    assert reverse("sitemap") == "/sitemap.xml"

    # ensure the included `twitch` namespace is present
    msg: str = f"Expected 'twitch:dashboard' to reverse to '/twitch/', got {reverse('twitch:dashboard')}"
    assert reverse("twitch:dashboard") == "/twitch/", msg


def test_debug_tools_not_present_while_testing() -> None:
    """`silk` and Django Debug Toolbar URL patterns are not present while running tests."""
    # Default test environment should *not* expose debug routes.
    mod = _reload_urls_with(TESTING=True)
    patterns = list(_pattern_strings(mod))
    assert not any("silk" in p for p in patterns)
    assert not any("__debug__" in p or "debug" in p for p in patterns)
