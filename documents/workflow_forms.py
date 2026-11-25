from django import forms
from django.contrib.auth import get_user_model

from .models import WORKFLOW_TEXT_LIMIT
from .people import full_name
from .rich_text import normalize


def active_recipients(owner):
    return (
        get_user_model()
        .objects.select_related("profile")
        .defer("profile__avatar")
        .filter(
            is_active=True,
        )
        .exclude(pk=owner.pk)
        .order_by("last_name", "first_name", "pk")
    )


def recipient_label(user):
    return full_name(user) or "ФИО не указано"


class TaskForm(forms.Form):
    title = forms.CharField(label="Название задачи", max_length=255, required=False)
    recipient = forms.ModelChoiceField(queryset=get_user_model().objects.none(), label="Кому отправить")
    due_date = forms.DateField(
        required=False, label="Срок", input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    purpose = forms.CharField(
        label="Что нужно сделать", max_length=WORKFLOW_TEXT_LIMIT, widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Опиши, что нужно сделать"})
    )

    def clean_purpose(self):
        value = normalize(self.cleaned_data["purpose"])
        if not value:
            raise forms.ValidationError("Напиши сообщение.")
        return value

    def __init__(self, owner, data=None):
        super().__init__(data)
        self.fields["recipient"].queryset = active_recipients(owner)
        self.fields["recipient"].label_from_instance = recipient_label


class CommentForm(forms.Form):
    comment = forms.CharField(
        label="Комментарий",
        max_length=WORKFLOW_TEXT_LIMIT,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Напишите комментарий к документу"}),
    )

    def clean_comment(self):
        return normalize(self.cleaned_data["comment"])
