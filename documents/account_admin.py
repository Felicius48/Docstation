from django.contrib.admin import AdminSite, ModelAdmin, StackedInline
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import DocumentType, UserProfile


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
    fields = ("position", "phone")
    can_delete = False


class AccountAdmin(UserAdmin):
    inlines = (ProfileInline,)
    filter_horizontal = ("groups",)
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Контакты", {"fields": ("first_name", "last_name", "email")}),
        ("Доступ", {"fields": ("is_active", "is_superuser", "groups")}),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    list_filter = ("is_active", "is_superuser", "groups")
    list_display = ("username", "email", "first_name", "last_name", "is_superuser")

    def has_delete_permission(self, request, obj=None):
        return False


class DocumentTypeAdmin(ModelAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")

    def get_readonly_fields(self, request, obj=None):
        return ("code",) if obj else ()

    def has_delete_permission(self, request, obj=None):
        return False


account_admin = AccountAdminSite(name="account_admin")
account_admin.register(get_user_model(), AccountAdmin)
account_admin.register(Group, DepartmentAdmin)
account_admin.register(DocumentType, DocumentTypeAdmin)
