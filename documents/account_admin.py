from django.contrib.admin import AdminSite, StackedInline
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.db import transaction

from .models import CompressionPreview, Result, UserProfile, Version
from .previews import preview_source
from .storage import remove_files


class AccountAdminSite(AdminSite):
    site_header = "DocStation · Управление доступом"
    site_title = "DocStation"

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def login(self, request, extra_context=None):
        if request.user.is_authenticated:
            if not self.has_permission(request):
                raise PermissionDenied
            return redirect("account_admin:index")
        return redirect_to_login(request.GET.get("next") or request.path)


class DepartmentAdmin(GroupAdmin):
    fields = ("name",)


class ProfileInline(StackedInline):
    model = UserProfile
    fields = ("patronymic", "position", "phone")
    can_delete = False


class AccountAdmin(UserAdmin):
    inlines = (ProfileInline,)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Личные данные", {"fields": ("last_name", "first_name", "email")}),
    )
    filter_horizontal = ("groups",)
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Личные данные", {"fields": ("last_name", "first_name", "email")}),
        ("Доступ", {"fields": ("is_active", "is_superuser", "groups")}),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    list_filter = ("is_active", "is_superuser", "groups")
    list_display = ("username", "email", "first_name", "last_name", "is_superuser")

    def delete_model(self, request, obj):
        keys = list(Version.objects.filter(document__owner=obj).values_list("key", flat=True))
        keys.extend(Result.objects.filter(job__owner=obj).values_list("key", flat=True))
        previews = list(CompressionPreview.objects.filter(owner=obj).values_list("pk", flat=True))
        super().delete_model(request, obj)

        def clean_files():
            remove_files(keys)
            for key in previews:
                preview_source(key).unlink(missing_ok=True)

        transaction.on_commit(clean_files, robust=True)

    actions = None

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (obj is None or obj.pk != request.user.pk)


account_admin = AccountAdminSite(name="account_admin")
account_admin.register(get_user_model(), AccountAdmin)
account_admin.register(Group, DepartmentAdmin)
