from django.conf import settings
from django.db import models
from django.db.models import Prefetch, Q

from app.models import Item


class CustomListManager(models.Manager):
    """Manager for custom lists."""

    def get_user_lists(self, user):
        """Return the custom lists that the user owns or collaborates on."""
        return (
            self.filter(Q(owner=user) | Q(collaborators=user))
            .prefetch_related(
                "collaborators",
                Prefetch(
                    "items",
                    queryset=Item.objects.order_by("-customlistitem__date_added"),
                ),
                Prefetch(
                    "customlistitem_set",
                    queryset=CustomListItem.objects.order_by("-date_added"),
                ),
            )
            .distinct()
        )


class CustomList(models.Model):
    """Model for custom lists."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    collaborators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="collaborated_lists",
        blank=True,
    )
    items = models.ManyToManyField(
        Item,
        related_name="custom_lists",
        blank=True,
        through="CustomListItem",
    )

    objects = CustomListManager()

    class Meta:
        """Meta options for the model."""

        ordering = ["name"]
        unique_together = ["name", "owner"]

    def __str__(self):
        """Return the name of the custom list."""
        return self.name

    def user_can_edit(self, user):
        """Check if the user can edit the list."""
        return self.owner == user

    def user_can_delete(self, user):
        """Check if the user can delete the list."""
        return self.owner == user

    def user_can_view(self, user):
        """Check if the user can view the list."""
        return self.owner == user or user in self.collaborators.all()


class CustomListItemManager(models.Manager):
    """Manager for custom list items."""

    def get_last_added_date(self, custom_list):
        """Return the last time an item was added to a specific list."""
        try:
            return self.filter(custom_list=custom_list).latest("date_added").date_added
        except self.model.DoesNotExist:
            return None


class CustomListItem(models.Model):
    """Model for items in custom lists."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    custom_list = models.ForeignKey(CustomList, on_delete=models.CASCADE)
    date_added = models.DateTimeField(auto_now_add=True)

    objects = CustomListItemManager()

    class Meta:
        """Meta options for the model."""

        ordering = ["date_added"]
        unique_together = ["item", "custom_list"]

    def __str__(self):
        """Return the name of the list item."""
        return self.item.title
