from typing import TYPE_CHECKING

from django.urls import path

from youtube import views

if TYPE_CHECKING:
    from django.urls.resolvers import URLPattern
    from django.urls.resolvers import URLResolver

app_name = "youtube"


urlpatterns: list[URLPattern | URLResolver] = [
    path(route="", view=views.index, name="index"),
]
