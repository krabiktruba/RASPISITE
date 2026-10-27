from django.contrib import admin
from .models import (
    TagType,
    Tag,
    Teacher,
    Course,
    Shift,
    Group,
    Pair,
    Weekday,
    Day,
    Calendar,
    StudyHours,
    BreakHours,
    ScheduleChange,
)

admin.site.register(TagType)
admin.site.register(Tag)
admin.site.register(Teacher)
admin.site.register(Course)
admin.site.register(Shift)
admin.site.register(Group)
admin.site.register(Pair)
admin.site.register(Weekday)
admin.site.register(Day)


@admin.register(Calendar)
class CalendarAdmin(admin.ModelAdmin):
    """Здесь отмечаются праздничные и нерабочие дни."""

    list_display = ("date", "weekday", "is_holiday", "holiday_name")
    list_editable = ("is_holiday", "holiday_name")
    list_filter = ("is_holiday",)
    date_hierarchy = "date"
    ordering = ("date",)
    readonly_fields = ("weekday",)

admin.site.register(StudyHours)
admin.site.register(BreakHours)
admin.site.register(ScheduleChange)