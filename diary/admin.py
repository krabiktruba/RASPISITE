from django.contrib import admin

from .models import Mark


@admin.register(Mark)
class MarkAdmin(admin.ModelAdmin):
    list_display = (
        "lesson_date", "pair_number", "student", "pair", "kind", "score",
        "teacher",
    )
    list_filter = ("kind", "lesson_date")
    list_select_related = ("student", "pair", "teacher")
    raw_id_fields = ("student", "pair", "teacher")
    date_hierarchy = "lesson_date"
