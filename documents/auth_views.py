from datetime import timedelta

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import RegistrationInvitation, RegistrationPolicy


class AccountView(LoginView):
    template_name = "documents/login.html"
    redirect_authenticated_user = True

    def invitation(self):
        return RegistrationInvitation.objects.filter(
            pk=self.kwargs.get("token"), expires_at__gt=timezone.now(),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        policy = RegistrationPolicy.objects.get(pk=1)
        invited = "token" in self.kwargs
        context["registration_allowed"] = self.invitation().exists() if invited else policy.public
        context["invitation_invalid"] = invited and not context["registration_allowed"]
        context["invited"] = invited
        context.setdefault("registration", UserCreationForm())
        return context

    def render_to_response(self, context, **kwargs):
        response = super().render_to_response(context, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
        return response

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") != "register":
            return super().post(request, *args, **kwargs)

        registration = UserCreationForm(request.POST)
        user = None
        with transaction.atomic():
            policy = RegistrationPolicy.objects.get(pk=1)
            invited = "token" in kwargs
            allowed = self.invitation().exists() if invited else policy.public
            if allowed and registration.is_valid():
                if invited:
                    allowed = bool(self.invitation().delete()[0])
                if allowed:
                    user = registration.save()
                    if policy.owner_id is None:
                        policy.owner = user
                        policy.save(update_fields=["owner"])
        if user is None:
            return self.render_to_response(self.get_context_data(
                form=self.get_form_class()(request=request), registration=registration,
            ), status=200 if allowed else 403)
        login(request, user)
        return redirect(self.get_success_url())


@login_required
@require_http_methods(["GET", "POST"])
def registration_settings(request):
    policy = RegistrationPolicy.objects.get(pk=1)
    if not (request.user.pk == policy.owner_id or request.user.is_superuser):
        raise Http404

    invitation_url = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action in {"open", "close"}:
            policy.public = action == "open"
            policy.save(update_fields=["public"])
            return redirect("registration_settings")
        if action != "invite":
            return HttpResponseBadRequest()
        invitation = RegistrationInvitation.objects.create(expires_at=timezone.now() + timedelta(days=7))
        invitation_url = request.build_absolute_uri(reverse("register_invited", args=[invitation.pk]))
    response = render(request, "documents/registration_settings.html", {
        "policy": policy, "invitation_url": invitation_url,
    })
    response["Cache-Control"] = "private, no-store"
    return response
