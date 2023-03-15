from decouple import config


def export_vars(request):
    data = {}
    data['REGISTRATION'] = config("REGISTRATION", default=True, cast=bool)
    return data
