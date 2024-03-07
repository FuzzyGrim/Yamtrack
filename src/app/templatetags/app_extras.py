from django import template
from unidecode import unidecode

register = template.Library()


@register.filter
def addslashes_double(arg1: str) -> str:
    """Add slashes before double quotes."""
    return arg1.replace('"', '\\"')


@register.filter()
def no_under(arg1: str) -> str:
    """Return the title case of the string."""
    return arg1.replace("_", " ")


@register.filter()
def slug(arg1: str) -> str:
    """Return the slug of the string."""
    return template.defaultfilters.slugify(unidecode(arg1))
