from django.db import models
from django.contrib.auth.models import User

class Media(models.Model):
    media_id = models.IntegerField()
    title = models.CharField(max_length=100)
    image = models.TextField()
    media_type = models.CharField(max_length=100)
    seasons = models.JSONField(default=dict)
    ind_score = models.FloatField(null=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=100)
    num_seasons = models.IntegerField(null=True)
    
    def __str__(self):
        return self.title

    class Meta:
        ordering = ['title']