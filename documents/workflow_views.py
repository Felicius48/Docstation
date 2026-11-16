from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from .models import Version, WorkflowRecord

State = WorkflowRecord.State
TRANSITIONS = {
    State.CREATED: (State.REVIEW, State.WITHDRAWN),
    State.REVIEW: (State.SIGNED, State.REJECTED, State.WITHDRAWN),
    State.SIGNED: (State.REGISTERED,),
    State.REGISTERED: (State.ARCHIVED,),
}


class RecordForm(forms.ModelForm):
    class Meta:
        model = WorkflowRecord
        fields = ["title", "kind", "purpose", "description"]
        labels = {"title": "Название", "kind": "Тип", "purpose": "Назначение", "description": "Описание"}
        widgets = {"purpose": forms.Textarea(attrs={"rows": 3}), "description": forms.Textarea(attrs={"rows": 3})}


@login_required
@require_safe
def workflow_records(request):
    records = WorkflowRecord.objects.filter(version__document__owner=request.user).order_by("-updated_at", "-pk")
    query = request.GET.get("q", "").strip()[:255]
    kind = request.GET.get("kind", "")
    state = request.GET.get("state", "")
    if query:
        records = records.filter(Q(title__icontains=query) | Q(purpose__icontains=query)
                                 | Q(description__icontains=query) | Q(registration_number__icontains=query))
    if kind in WorkflowRecord.Kind.values:
        records = records.filter(kind=kind)
    if state in State.values:
        records = records.filter(state=state)
    return render(request, "documents/workflow_list.html", {
        "page": Paginator(records, 30).get_page(request.GET.get("page")), "query": query,
        "kinds": WorkflowRecord.Kind.choices, "states": State.choices, "kind": kind, "state": state,
    })


@login_required
@require_http_methods(["GET", "POST"])
def workflow_create(request, pk):
    if request.method == "POST":
        with transaction.atomic():
            version = get_object_or_404(Version, pk=pk, document__owner=request.user)
            form = RecordForm(request.POST)
            if form.is_valid():
                record = form.save(commit=False)
                record.version = version
                record.save()
                record.events.create(actor=request.user, state=record.state)
                return redirect("workflow_detail", record.pk)
    else:
        version = get_object_or_404(Version.objects.select_related("document"), pk=pk, document__owner=request.user)
        form = RecordForm(initial={"title": version.document.title})
    return render(request, "documents/workflow_create.html", {"form": form, "version": version})


@login_required
@require_http_methods(["GET", "POST"])
def workflow_detail(request, pk):
    records = WorkflowRecord.objects.filter(version__document__owner=request.user).select_related("version")
    if request.method == "POST":
        with transaction.atomic():
            record = get_object_or_404(records, pk=pk)
            target = request.POST.get("state")
            comment = request.POST.get("comment", "").strip()
            if target not in TRANSITIONS.get(record.state, ()):
                return HttpResponseBadRequest("Недопустимый переход статуса.")
            if len(comment) > 4000 or (target in (State.WITHDRAWN, State.REJECTED) and not comment):
                return HttpResponseBadRequest("Укажи причину отклонения или отзыва, до 4000 символов.")
            previous = record.state
            record.state = target
            if target == State.REGISTERED:
                now = timezone.now()
                prefix = f"{record.kind}-{timezone.localtime(now).year}-"
                count = WorkflowRecord.objects.filter(registration_number__startswith=prefix).count()
                record.registration_number = f"{prefix}{count + 1:06d}"
                record.registered_at = now
            record.save(update_fields=["state", "updated_at", "registration_number", "registered_at"])
            record.events.create(actor=request.user, previous_state=previous, state=target, comment=comment)
        return redirect("workflow_detail", pk)
    record = get_object_or_404(records, pk=pk)
    return render(request, "documents/workflow_detail.html", {
        "record": record, "events": record.events.select_related("actor").order_by("pk"),
        "transitions": [(state, state.label) for state in TRANSITIONS.get(record.state, ())],
    })
