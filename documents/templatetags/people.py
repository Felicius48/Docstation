from django import template

from ..people import full_name

register = template.Library()
register.filter(full_name)
