from django import template

from ..people import full_name

register = template.Library()
register.filter(full_name)


@register.filter
def initials(user):
    parts = full_name(user).split()[:2]
    return "".join(part[0] for part in parts).upper() or user.username[:1].upper()
