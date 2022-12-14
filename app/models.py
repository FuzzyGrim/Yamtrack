from django.db import models
from django.contrib.auth.models import User

class Media(models.Model):
    media_id = models.IntegerField()
    title = models.CharField(max_length=100)
    description = models.TextField()
    image = models.TextField()
    year = models.IntegerField()
    media_type = models.CharField(max_length=100)
    seasons = models.JSONField(default=dict)
    ind_score = models.FloatField()
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    
    def __str__(self):
        return self.title