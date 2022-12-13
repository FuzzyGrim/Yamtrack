from django.db import models
from django.contrib.auth.models import User

class Media(models.Model):
    code = models.IntegerField()
    title = models.CharField(max_length=100)
    description = models.TextField()
    image = models.TextField()
    year = models.IntegerField()
    content = models.CharField(max_length=10)
    seasons = models.IntegerField()
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    
    def __str__(self):
        return self.title