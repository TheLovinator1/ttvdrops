from django.template import Context
from django.template import Template
from django.test import SimpleTestCase
from django.test import override_settings
from django.utils.safestring import SafeString

from twitch.models import Game
from twitch.templatetags.image_tags import get_format_url
from twitch.templatetags.image_tags import picture


@override_settings(MEDIA_URL="/media/", STATIC_URL="/static/")
class ImageTagsTests(SimpleTestCase):
    """Tests for image template tags and related functionality."""

    def test_picture_empty_src_returns_empty(self) -> None:
        """Test that picture tag with empty src returns empty string."""
        result = picture("")
        assert not str(result)

    def test_picture_keeps_external_url(self) -> None:
        """Test that picture tag does not modify external URLs and does not attempt format conversion."""
        src = "https://example.com/images/sample.png"
        result: SafeString = picture(src, alt="alt", width=16, height=16)
        rendered = str(result)

        # Should still contain the original external URL
        assert src in rendered

    def test_model_init_with_missing_image_does_not_raise(self) -> None:
        """Test that initializing a model with a missing image file does not raise an error."""
        # Simulate a Game instance with a missing local image file. The
        # AppConfig.ready() wrapper should prevent FileNotFoundError during
        # model initialization.
        g = Game(twitch_id="test-game", box_art_file="campaigns/images/missing.png")
        # If initialization reached this point without raising, we consider
        # the protection successful.
        assert g.twitch_id == "test-game"


class TestGetFormatUrl:
    """Tests for the get_format_url helper function."""

    def test_empty_url(self) -> None:
        """Test that empty URL returns empty string."""
        assert not get_format_url("", "webp")

    def test_jpg_to_webp(self) -> None:
        """Test converting JPG to WebP."""
        assert (
            get_format_url("/static/img/banner.jpg", "webp")
            == "/static/img/banner.webp"
        )

    def test_jpeg_to_avif(self) -> None:
        """Test converting JPEG to AVIF."""
        assert (
            get_format_url("/static/img/photo.jpeg", "avif") == "/static/img/photo.avif"
        )

    def test_png_to_webp(self) -> None:
        """Test converting PNG to WebP."""
        assert get_format_url("/static/img/logo.png", "webp") == "/static/img/logo.webp"

    def test_uppercase_extension(self) -> None:
        """Test converting uppercase extensions."""
        assert (
            get_format_url("/static/img/photo.JPG", "webp") == "/static/img/photo.webp"
        )

    def test_non_convertible_format(self) -> None:
        """Test that non-convertible formats return unchanged."""
        svg_url = "/static/img/icon.svg"
        assert get_format_url(svg_url, "webp") == svg_url

        gif_url = "/static/img/animated.gif"
        assert get_format_url(gif_url, "avif") == gif_url

    def test_url_with_query_params(self) -> None:
        """Test URL with query parameters preserves them."""
        result = get_format_url("/static/img/photo.jpg?v=123", "webp")
        assert result == "/static/img/photo.webp?v=123"

    def test_full_url(self) -> None:
        """Test full URL with domain."""
        result: str = get_format_url("https://example.com/img/photo.jpg", "avif")
        assert result == "https://example.com/img/photo.avif"


class TestPictureTag:
    """Tests for the picture template tag."""

    def test_empty_src(self) -> None:
        """Test that empty src returns empty string."""
        result: SafeString = picture("")
        assert not result

    def test_basic_picture(self) -> None:
        """Test basic picture tag with minimal parameters."""
        result: SafeString = picture("/static/img/photo.jpg")

        # Should contain picture element
        assert "<picture>" in result
        assert "</picture>" in result

        # Should have AVIF and WebP sources
        assert '<source srcset="/static/img/photo.avif" type="image/avif" />' in result
        assert '<source srcset="/static/img/photo.webp" type="image/webp" />' in result

        # Should have fallback img tag
        assert '<img src="/static/img/photo.jpg"' in result
        assert 'loading="lazy"' in result

    def test_all_attributes(self) -> None:
        """Test picture tag with all optional attributes."""
        result: SafeString = picture(
            src="/static/img/photo.jpg",
            alt="Test photo",
            width=800,
            height=600,
            loading="eager",
            css_class="rounded shadow",
            style="max-width: 100%",
        )

        assert 'alt="Test photo"' in result
        assert 'width="800"' in result
        assert 'height="600"' in result
        assert 'loading="eager"' in result
        assert 'class="rounded shadow"' in result
        assert 'style="max-width: 100%"' in result

    def test_non_convertible_format(self) -> None:
        """Test that non-convertible formats don't generate source tags."""
        result: SafeString = picture("/static/img/icon.svg")

        # Should not have source tags since SVG can't be converted
        assert "<source" not in result

        # Should still have picture and img tags
        assert "<picture>" in result
        assert '<img src="/static/img/icon.svg"' in result
        assert "</picture>" in result

    def test_xss_prevention_in_src(self) -> None:
        """Test that XSS attempts in src are escaped."""
        malicious_src = '"><script>alert("xss")</script><img src="'
        result = picture(malicious_src)

        # Should escape the malicious code
        assert "<script>" not in result
        assert "</script>" not in result
        assert "&quot;" in result

    def test_xss_prevention_in_alt(self) -> None:
        """Test that XSS attempts in alt text are escaped."""
        result: SafeString = picture(
            "/static/img/photo.jpg",
            alt='"><script>alert("xss")</script>',
        )

        # Should escape the malicious code
        assert "<script>" not in result
        assert "</script>" not in result
        assert "&quot;" in result

    def test_xss_prevention_in_css_class(self) -> None:
        """Test that XSS attempts in CSS class are escaped."""
        result: SafeString = picture(
            "/static/img/photo.jpg",
            css_class='"><script>alert("xss")</script>',
        )

        # Should escape the malicious code
        assert "<script>" not in result
        assert "</script>" not in result

    def test_xss_prevention_in_style(self) -> None:
        """Test that XSS attempts in style are escaped."""
        result: SafeString = picture(
            "/static/img/photo.jpg",
            style='"><script>alert("xss")</script>',
        )

        # Should escape the malicious code
        assert "<script>" not in result
        assert "</script>" not in result

    def test_returns_safestring(self) -> None:
        """Test that the result is a SafeString."""
        result: SafeString = picture("/static/img/photo.jpg")
        assert isinstance(result, SafeString)

    def test_alt_empty_string(self) -> None:
        """Test that alt="" includes empty alt attribute."""
        result: SafeString = picture("/static/img/photo.jpg", alt="")
        assert 'alt=""' in result

    def test_no_width_or_height(self) -> None:
        """Test that missing width/height are not included."""
        result: SafeString = picture("/static/img/photo.jpg")

        # Should not have width or height attributes
        assert 'width="' not in result
        assert 'height="' not in result

    def test_url_with_query_params(self) -> None:
        """Test that query parameters are preserved in formats."""
        result: SafeString = picture("/static/img/photo.jpg?v=123")

        assert "/static/img/photo.avif?v=123" in result
        assert "/static/img/photo.webp?v=123" in result
        assert "/static/img/photo.jpg?v=123" in result

    def test_full_url(self) -> None:
        """Test with full URL including domain."""
        result: SafeString = picture("https://cdn.example.com/images/photo.jpg")

        assert "https://cdn.example.com/images/photo.avif" in result
        assert "https://cdn.example.com/images/photo.webp" in result
        assert "https://cdn.example.com/images/photo.jpg" in result

    def test_twitch_cdn_url_simple_img(self) -> None:
        """Test that Twitch CDN URLs return simple img tag without picture element."""
        result: SafeString = picture(
            "https://static-cdn.jtvnw.net/ttv-boxart/1292861145.jpg",
        )

        # Should NOT have picture element
        assert "<picture>" not in result
        assert "</picture>" not in result

        # Should NOT have source tags for format conversion
        assert "<source" not in result

        # Should have simple img tag
        assert 'src="https://static-cdn.jtvnw.net/ttv-boxart/1292861145.jpg"' in result
        assert 'loading="lazy"' in result

    def test_twitch_cdn_url_with_attributes(self) -> None:
        """Test Twitch CDN URL with optional attributes."""
        result: SafeString = picture(
            "https://static-cdn.jtvnw.net/ttv-boxart/1292861145.jpg",
            alt="Game art",
            width=300,
            height=400,
            loading="eager",
            css_class="game-cover",
            style="border-radius: 8px",
        )

        # Should still be simple img tag
        assert "<picture>" not in result
        assert "</picture>" not in result
        assert "<source" not in result

        # Should have all attributes
        assert 'src="https://static-cdn.jtvnw.net/ttv-boxart/1292861145.jpg"' in result
        assert 'alt="Game art"' in result
        assert 'width="300"' in result
        assert 'height="400"' in result
        assert 'loading="eager"' in result
        assert 'class="game-cover"' in result
        assert 'style="border-radius: 8px"' in result

    def test_twitch_cdn_url_with_png(self) -> None:
        """Test Twitch CDN URL with PNG format."""
        result: SafeString = picture(
            "https://static-cdn.jtvnw.net/badges/v1/1234567.png",
        )

        # Should NOT have picture element or source tags
        assert "<picture>" not in result
        assert "</picture>" not in result
        assert "<source" not in result

        # Should have simple img tag
        assert 'src="https://static-cdn.jtvnw.net/badges/v1/1234567.png"' in result


class TestPictureTagTemplate:
    """Tests for the picture tag used in templates."""

    def test_picture_tag_in_template(self) -> None:
        """Test that the picture tag works when called from a template."""
        template = Template(
            '{% load image_tags %}{% picture src="/img/photo.jpg" alt="Test" %}',
        )
        context = Context({})
        result: SafeString = template.render(context)

        assert "<picture>" in result
        assert "</picture>" in result
        assert '<source srcset="/img/photo.avif"' in result
        assert '<source srcset="/img/photo.webp"' in result
        assert '<img src="/img/photo.jpg"' in result
        assert 'alt="Test"' in result

    def test_picture_tag_with_context_variables(self) -> None:
        """Test using context variables in the picture tag."""
        template = Template(
            "{% load image_tags %}{% picture src=image_url alt=image_alt width=image_width %}",
        )
        context = Context({
            "image_url": "/img/banner.png",
            "image_alt": "Banner image",
            "image_width": 1200,
        })
        result: SafeString = template.render(context)

        assert '<img src="/img/banner.png"' in result
        assert 'alt="Banner image"' in result
        assert 'width="1200"' in result
        assert "/img/banner.avif" in result

    def test_picture_tag_with_no_parameters(self) -> None:
        """Test that the tag requires at least src parameter."""
        # This should work with empty src but return empty string
        template = Template('{% load image_tags %}{% picture src="" %}')
        context = Context({})
        result: SafeString = template.render(context)

        assert not result.strip()
