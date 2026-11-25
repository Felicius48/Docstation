from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from ..rich_text import PREFIX, RichText, normalize_newlines

register = template.Library()


@register.filter
def rich_message(value):
    value = normalize_newlines(value or "")
    if value.startswith(PREFIX):
        return mark_safe("".join(RichText(value.removeprefix(PREFIX)).html))
    return escape(value)


@register.filter
def message_summary(value):
    value = normalize_newlines(value or "")
    if value.startswith(PREFIX):
        return " ".join("".join(RichText(value.removeprefix(PREFIX)).text).split())
    return value
