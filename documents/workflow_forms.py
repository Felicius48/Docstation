from django import forms
from django.contrib.auth import get_user_model

from .models import WORKFLOW_TEXT_LIMIT
from .people import full_name


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
    recipient = forms.ModelChoiceField(queryset=get_user_model().objects.none(), label="Кому отправить")
    purpose = forms.CharField(
        label="Что нужно сделать", max_length=WORKFLOW_TEXT_LIMIT, widget=forms.Textarea(attrs={"rows": 4})
    )

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
