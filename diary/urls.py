from django.urls import path

from . import journal_views, views

urlpatterns = [
    path("", views.diary_home, name="diary_home"),
    path("journal/", journal_views.diary_journal, name="diary_journal"),
    path(
        "journal/general/",
        journal_views.diary_journal_general,
        name="diary_journal_general",
    ),
    path("group/<int:group_id>/", views.diary_group, name="diary_group"),
]
