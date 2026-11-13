from django import template

from ..file_responses import preview_type

register = template.Library()
register.filter(preview_type)
