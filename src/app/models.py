from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings


class Media(models.Model):
    media_id = models.IntegerField()
    title = models.CharField(max_length=100)
    image = models.ImageField()
    media_type = models.CharField(max_length=30)
    score = models.DecimalField(null=True, max_digits=3, decimal_places=1)
    progress = models.IntegerField()
    status = models.CharField(max_length=30)
    # Allow null values for start date for myanimelist and anilist imports
    start_date = models.DateField(null=True)
    end_date = models.DateField(null=True)
    api = models.CharField(max_length=10)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )

    def __str__(self):
        return self.title

    class Meta:
        ordering = ["-score"]


class Season(models.Model):
    parent = models.ForeignKey(
        Media, on_delete=models.CASCADE, related_name="seasons"
    )
    title = models.CharField(max_length=100)
    number = models.IntegerField()
    score = models.DecimalField(null=True, max_digits=3, decimal_places=1)
    status = models.CharField(max_length=30)
    progress = models.IntegerField()
    # Allow null values for start date for myanimelist and anilist imports
    start_date = models.DateField(null=True)
    end_date = models.DateField(null=True)

    def __str__(self):
        return f"{self.title} - Season {self.number}"

    class Meta:
        ordering = ["parent", "number"]


class User(AbstractUser):
    last_search_type = models.CharField(max_length=10, default="tv")
