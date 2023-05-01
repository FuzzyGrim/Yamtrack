from decouple import config


def export_vars(request):
    return {"REGISTRATION": config("REGISTRATION", default=True, cast=bool)}
