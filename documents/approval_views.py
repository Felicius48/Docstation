from contextlib import nullcontext

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_safe, require_http_methods

from .archive import save_version
from .storage import remove_files
from .uploads import request_file
from .models import Approval, ApprovalDecision, ApprovalRound, Version
from .file_responses import download

State = ApprovalRound.State


class RoundForm(forms.Form):
    version = forms.ModelChoiceField(label="Версия", queryset=Version.objects.none())
    reviewers = forms.ModelMultipleChoiceField(label="Согласующие", queryset=get_user_model().objects.none())
    comment = forms.CharField(label="Комментарий", required=False, max_length=4000, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, document, owner, data=None, *, after=0, initial=None):
        super().__init__(data, initial=initial)
        if document is None:
            self.fields.pop("version")
        else:
            self.fields["version"].queryset = document.versions.filter(number__gt=after)
            self.fields["version"].label_from_instance = lambda version: f"Версия {version.number} · {version.name}"
        self.fields["reviewers"].queryset = get_user_model().objects.filter(is_active=True).exclude(pk=owner.pk).order_by("username")


class DraftForm(RoundForm):
    title = forms.CharField(label="Название", max_length=255)
    field_order = ["title", "version", "reviewers", "comment"]


def add_round(approval, data):
    round = approval.rounds.create(version=data["version"], comment=data["comment"])
    ApprovalDecision.objects.bulk_create([
        ApprovalDecision(round=round, reviewer=reviewer) for reviewer in data["reviewers"]
    ])


def visible_rounds(user):
    assignments = ApprovalDecision.objects.filter(round_id=OuterRef("pk"), reviewer=user)
    return ApprovalRound.objects.filter(
        Q(approval__document__owner=user) | (Q(sent_at__isnull=False) & Q(Exists(assignments))),
    )


@login_required
@require_safe
def approvals(request, status="inbox"):
    labels = {"inbox": "Мне на согласование", "all": "Все документы", **dict(State.choices)}
    if status not in labels:
        raise Http404

    assignments = ApprovalDecision.objects.filter(round_id=OuterRef("pk"), reviewer=request.user)
    visible = visible_rounds(request.user)
    latest = visible.filter(approval_id=OuterRef("approval_id")).order_by("-pk")
    rounds = visible.filter(pk=Subquery(latest.values("pk")[:1])).annotate(
        needs_decision=Exists(assignments.filter(decision="")),
    )
    inbox = rounds.filter(state=State.OPEN, needs_decision=True)
    counts = dict(rounds.order_by().values("state").annotate(total=Count("pk")).values_list("state", "total"))
    counts.update(inbox=inbox.count(), all=sum(counts.values()))
    selected = inbox if status == "inbox" else rounds
    if status in State.values:
        selected = selected.filter(state=status)
    selected = selected.select_related("approval__document__owner", "version").order_by("-pk")
    return render(request, "documents/approvals.html", {
        "tabs": [(key, label, counts.get(key, 0)) for key, label in labels.items()],
        "status": status,
        "heading": labels[status],
        "page": Paginator(selected, 30).get_page(request.GET.get("page")),
    })


@login_required
@require_http_methods(["GET", "POST"])
def approval_new(request, pk=None):
    version = None
    if pk is not None:
        version = get_object_or_404(
            Version.objects.select_related("document"), pk=pk, document__owner=request.user,
        )
    document = version.document if version else None
    form = DraftForm(document, request.user, request.POST if request.method == "POST" else None,
                     initial={"version": version, "title": document.title} if document else None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        uploaded = None
        try:
            with transaction.atomic():
                if document is None:
                    upload = request_file(request, "Выбери файл для согласования.")
                    uploaded = save_version(request.user, upload)
                    data["version"] = uploaded
                else:
                    data["version"] = get_object_or_404(
                        Version.objects.select_related("document"), pk=data["version"].pk,
                        document__owner=request.user,
                    )
                approval = Approval.objects.create(document=data["version"].document, title=data["title"])
                add_round(approval, data)
        except ValueError as exc:
            if uploaded:
                remove_files([uploaded.key])
            form.add_error(None, str(exc))
        except BaseException:
            if uploaded:
                remove_files([uploaded.key])
            raise
        else:
            return redirect("approval_detail", approval.pk)
    return render(request, "documents/approval_new.html", {"form": form, "upload_new": document is None})


@login_required
@require_http_methods(["GET", "POST"])
def approval_detail(request, pk):
    with transaction.atomic() if request.method == "POST" else nullcontext():
        approval = get_object_or_404(Approval.objects.select_related("document__owner"), pk=pk)
        author = approval.document.owner_id == request.user.pk
        rounds = visible_rounds(request.user).filter(approval=approval)
        rounds = list(rounds.select_related("version").prefetch_related("decisions__reviewer"))
        if not rounds:
            raise Http404
        current = rounds[-1]
        assignment = next((item for item in current.decisions.all() if item.reviewer_id == request.user.pk), None)
        can_decide = current.state == State.OPEN and assignment is not None and not assignment.decision
        form = None
        if author and current.state == State.RETURNED:
            form = RoundForm(approval.document, request.user,
                             request.POST if request.POST.get("action") == "revise" else None,
                             after=current.version.number,
                             initial={
                                 "version": approval.document.versions.filter(number__gt=current.version.number).first(),
                                 "reviewers": [item.reviewer_id for item in current.decisions.all()],
                             })

        if request.method == "POST":
            if request.POST.get("round") != str(current.pk):
                return HttpResponseBadRequest("Цикл согласования изменился. Обновите страницу.")
            action = request.POST.get("action")
            now = timezone.now()
            if author and action == "discard" and current.state == State.DRAFT and current.sent_at is None:
                current.delete()
                if len(rounds) == 1:
                    approval.delete()
                    return redirect("approval_status", status="draft")
                return redirect("approval_detail", pk)
            if author and action == "send" and current.state == State.DRAFT:
                current.state, current.sent_at = State.OPEN, now
                current.save(update_fields=["state", "sent_at"])
            elif author and action == "withdraw" and current.state == State.OPEN:
                current.state, current.closed_at = State.WITHDRAWN, now
                current.save(update_fields=["state", "closed_at"])
            elif action == "upload_revision" and form is not None:
                try:
                    upload = request_file(request, "Выбери исправленный файл.")
                    save_version(request.user, upload, document_id=approval.document_id)
                except ValueError as exc:
                    return render_card(request, approval, rounds, current, author, can_decide, form, str(exc))
            elif action == "revise" and form is not None:
                if not form.is_valid():
                    return render_card(request, approval, rounds, current, author, can_decide, form)
                add_round(approval, form.cleaned_data)
            elif can_decide and action in ApprovalDecision.Choice.values:
                comment = request.POST.get("comment", "").strip()
                if len(comment) > 4000 or (action == ApprovalDecision.Choice.RETURN and not comment):
                    return render_card(request, approval, rounds, current, author, can_decide, form,
                                       "Укажите причину возврата. Максимум 4000 символов.")
                assignment.decision, assignment.comment, assignment.decided_at = action, comment, now
                assignment.save(update_fields=["decision", "comment", "decided_at"])
                if action == ApprovalDecision.Choice.RETURN:
                    current.state = State.RETURNED
                elif not current.decisions.filter(decision="").exists():
                    current.state = State.APPROVED
                if current.state != State.OPEN:
                    current.closed_at = now
                    current.save(update_fields=["state", "closed_at"])
            else:
                return HttpResponseBadRequest("Действие недоступно.")
            return redirect("approval_detail", pk)
        return render_card(request, approval, rounds, current, author, can_decide, form)


def render_card(request, approval, rounds, current, author, can_decide, form, error=""):
    for cycle in rounds:
        decisions = sorted(
            (item for item in cycle.decisions.all() if item.decided_at),
            key=lambda item: (item.decided_at, item.pk),
        )
        cycle.timeline = [
            ("Версия добавлена в архив", cycle.version.created_at, approval.document.owner, ""),
            ("Создан черновик", cycle.created_at, approval.document.owner, cycle.comment),
        ]
        if cycle.sent_at:
            cycle.timeline.append(("На согласовании", cycle.sent_at, approval.document.owner, ""))
        cycle.timeline.extend(
            (item.get_decision_display(), item.decided_at, item.reviewer, item.comment)
            for item in decisions
        )
        if cycle.closed_at:
            actor = approval.document.owner if cycle.state == State.WITHDRAWN else (
                decisions[-1].reviewer if decisions else None
            )
            cycle.timeline.append((cycle.get_state_display(), cycle.closed_at, actor, ""))

    assignments = list(current.decisions.all())
    returned = next((item for item in assignments if item.decision == ApprovalDecision.Choice.RETURN), None)
    return render(request, "documents/approval_detail.html", {
        "approved_count": sum(item.decision == ApprovalDecision.Choice.APPROVE for item in assignments),
        "reviewer_count": len(assignments),
        "returned": returned,
        "approval": approval, "rounds": rounds, "current": current, "author": author,
        "can_decide": can_decide, "form": form, "error": error,
    })


@login_required
@require_safe
def approval_file(request, pk, preview=False):
    rounds = visible_rounds(request.user).select_related("version")
    version = get_object_or_404(rounds, pk=pk).version
    return download(version.key, version.name, preview=preview)
