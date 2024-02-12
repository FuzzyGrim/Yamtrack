from django import template

register = template.Library()


@register.filter
def addslashes_double(arg1: str) -> str:
    """Add slashes before double quotes."""
    return arg1.replace('"', '\\"')
