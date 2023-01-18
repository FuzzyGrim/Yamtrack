from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from app.storage import OverwriteStorage

class Media(models.Model):
    media_id = models.IntegerField()
    title = models.CharField(max_length=100)
    image = models.ImageField(upload_to="images", storage=OverwriteStorage())
    media_type = models.CharField(max_length=30)
    score = models.FloatField(null=True)
    progress = models.IntegerField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(max_length=30)
    num_seasons = models.IntegerField()
    api = models.CharField(max_length=10)
    
    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-score']


class Season(models.Model):
    media = models.ForeignKey(Media, on_delete=models.CASCADE, related_name='seasons')
    title = models.CharField(max_length=100)
    number = models.IntegerField()
    score = models.FloatField(null=True)
    status = models.CharField(max_length=30)
    progress = models.IntegerField()

    def __str__(self):
        return f"{self.title} - Season {self.number}"

    class Meta:
        ordering = ['media_id', 'number']


class User(AbstractUser):
    default_api = models.CharField(max_length=10, default="tmdb")
