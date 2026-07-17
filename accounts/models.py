from django.db import models
from django.contrib.auth.models import User


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio = models.TextField(blank=True, default='')
    display_name = models.CharField(max_length=100, blank=True, default='')
    avatar_glow_color = models.CharField(max_length=20, blank=True, default='#8b5cf6')
    advanced_mode = models.BooleanField(default=False)
    user_bubble_color = models.CharField(max_length=100, blank=True, default='')
    ai_bubble_color = models.CharField(max_length=100, blank=True, default='')

    def __str__(self):
        return f"{self.user.username}'s profile"

    @property
    def name_display(self):
        return self.display_name or self.user.username


class UserMemory(models.Model):
    """Small, explicit preference memory used to personalize future chats."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='ai_memory')
    preferred_language = models.CharField(max_length=20, blank=True, default='')
    preferences = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AI memory for {self.user.username}"
