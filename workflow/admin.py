from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (CaseEvent, Deadline, Department, DocumentVersion, ProjectCase,
                     RefereeAssignment, Review, User)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("نقش در سامانه", {"fields": ("user_type", "department", "is_department_head",
                                      "student_number", "expertise")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("مشخصات و نقش در سامانه", {"fields": ("first_name", "last_name", "user_type",
                                               "department", "is_department_head",
                                               "student_number", "expertise")}),
    )
    list_display = ("username", "first_name", "last_name", "user_type", "department", "is_department_head")
    list_filter = ("user_type", "department", "is_department_head")


class RefereeInline(admin.TabularInline):
    model = RefereeAssignment
    fk_name = "case"
    extra = 0


class VersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0


class DeadlineInline(admin.TabularInline):
    model = Deadline
    extra = 0


class EventInline(admin.TabularInline):
    model = CaseEvent
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProjectCase)
class ProjectCaseAdmin(admin.ModelAdmin):
    list_display = ("title_fa", "author", "advisor", "state", "academic_year", "semester")
    list_filter = ("state", "semester", "department")
    search_fields = ("title_fa", "title_en", "author__username", "author__last_name")
    inlines = [RefereeInline, VersionInline, DeadlineInline, EventInline]


admin.site.register(Department)
admin.site.register(Review)
