class MediaTypeChecker:
    regex = "(anime|manga|tv|movie)"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


class StatusChecker:
    regex = "(completed|watching|paused|dropped|planning)"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value
