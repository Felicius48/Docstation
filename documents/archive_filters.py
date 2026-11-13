from django import forms

from .operations import SUPPORTED, TOOLS


class ArchiveFilters(forms.Form):
    q = forms.CharField(label="Название", max_length=255, required=False)
    start = forms.DateField(label="С даты", required=False, input_formats=["%Y-%m-%d"],
                            widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="По дату", required=False, input_formats=["%Y-%m-%d"],
                          widget=forms.DateInput(attrs={"type": "date"}))
    format = forms.ChoiceField(label="Формат исходника", required=False, choices=[
        ("", "Все форматы"), *((ext, ext.upper()) for ext in sorted(SUPPORTED - {"jpeg", "tif"})),
    ])
    operation = forms.ChoiceField(label="Операция", required=False, choices=[
        ("", "Все операции"), *((key, tool[0]) for key, tool in TOOLS.items()),
    ])

    def clean(self):
        data = super().clean()
        start, end = data.get("start"), data.get("end")
        if start and end and start > end:
            raise forms.ValidationError("Начальная дата должна быть раньше конечной или совпадать с ней.")
        return data
