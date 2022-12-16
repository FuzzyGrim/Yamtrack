from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings


class Media(models.Model):
    media_id = models.IntegerField()
    title = models.CharField(max_length=100)
    image = models.TextField()
    media_type = models.CharField(max_length=100)
    seasons = models.JSONField(default=dict)
    ind_score = models.FloatField(null=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(max_length=30)
    num_seasons = models.IntegerField(null=True)
    api_origin = models.CharField(max_length=10)
    
    def __str__(self):
        return self.title

    class Meta:
        ordering = ['title']

class User(AbstractUser):
    default_api = models.CharField(max_length=10, default="tmdb")
