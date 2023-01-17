from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from app.storage import OverwriteStorage

class Media(models.Model):
    media_id = models.IntegerField()
    title = models.CharField(max_length=100)
    image = models.ImageField(upload_to="images", storage=OverwriteStorage())
    media_type = models.CharField(max_length=100)
    # track score and status for each season
    seasons_details = models.JSONField(default=dict)
    score = models.FloatField(null=True)
    progress = models.IntegerField(default=0)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(max_length=30)
    num_seasons = models.IntegerField(null=True)
    api = models.CharField(max_length=10)
    
    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-score']


class User(AbstractUser):
    default_api = models.CharField(max_length=10, default="tmdb")
