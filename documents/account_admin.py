from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


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


class AccountAdmin(UserAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


account_admin = AccountAdminSite(name="account_admin")
account_admin.register(get_user_model(), AccountAdmin)
account_admin.register(Group, GroupAdmin)
