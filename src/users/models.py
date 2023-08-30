from django.contrib.auth.models import AbstractUser
from django.db import models


# Create your models here.
class User(AbstractUser):
    """Custom user model that saves the last media search type."""

    last_search_type = models.CharField(max_length=10, default="tv", choices=[
        ("tv", "tv"),
        ("movie", "movie"),
        ("anime", "anime"),
        ("manga", "manga"),
    ])
