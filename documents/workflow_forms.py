from django import forms
from django.contrib.auth import get_user_model

from .models import WORKFLOW_TEXT_LIMIT, DocumentType, WorkflowRecord
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


class RecipientPicker(forms.CheckboxSelectMultiple):
    template_name = "documents/recipient_picker.html"


class RecipientForm(forms.ModelForm):
    class Meta:
        model = WorkflowRecord
        fields = ["recipients"]
        labels = {"recipients": "Кому направлен документ"}
        widgets = {"recipients": RecipientPicker()}

    def __init__(self, owner, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recipients"].queryset = active_recipients(owner)
        self.fields["recipients"].label_from_instance = recipient_label


class DetailsForm(forms.ModelForm):
    class Meta:
        model = WorkflowRecord
        fields = ["title", "purpose", "description"]
        labels = {"title": "Название", "purpose": "Назначение", "description": "Описание"}
        widgets = {
            "purpose": forms.Textarea(attrs={"rows": 3}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class RecordForm(RecipientForm):
    def __init__(self, owner, *args, **kwargs):
        super().__init__(owner, *args, **kwargs)
        self.fields["kind"].queryset = DocumentType.objects.filter(is_active=True)
        self.fields["mode"].choices = [
            (mode, label) for mode, label in WorkflowRecord.Mode.choices if mode != WorkflowRecord.Mode.TASK
        ]

    class Meta(RecipientForm.Meta):
        model = WorkflowRecord
        fields = ["mode", "title", "kind", "purpose", "description", "recipients"]
        labels = {
            **DetailsForm.Meta.labels,
            **RecipientForm.Meta.labels,
            "kind": "Тип",
            "mode": "Режим работы",
        }
        widgets = {**DetailsForm.Meta.widgets, **RecipientForm.Meta.widgets, "mode": forms.RadioSelect()}
        help_texts = {"mode": "Выберите режим до создания карточки."}


class CommentForm(forms.Form):
    comment = forms.CharField(
        label="Комментарий",
        max_length=WORKFLOW_TEXT_LIMIT,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Напишите комментарий к документу"}),
    )
