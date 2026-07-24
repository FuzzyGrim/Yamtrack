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
            .select_related("owner")
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

    def get_public_lists_for_user(self, user):
        """Return public lists owned by the given user."""
        return (
            self.filter(owner=user, is_public=True)
            .select_related("owner")
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=Item.objects.order_by("-customlistitem__date_added"),
                ),
            )
            .order_by("-id")
        )

    def get_private_item_ids(self, user):
        """Return Item IDs that should be hidden from other users.

        An item is private if it exists exclusively in private lists
        (not in any public list). Tracking status does not affect privacy.

        For TV shows in private lists, all related season and episode
        items are also included to ensure full show privacy.
        """
        from app.models import MediaTypes

        private_item_ids = CustomListItem.objects.filter(
            custom_list__owner=user,
            custom_list__is_public=False,
        ).values_list("item_id", flat=True)

        public_item_ids = CustomListItem.objects.filter(
            custom_list__owner=user,
            custom_list__is_public=True,
        ).values_list("item_id", flat=True)

        exclusively_private = set(private_item_ids) - set(public_item_ids)

        if not exclusively_private:
            return []

        tv_item_ids = Item.objects.filter(
            id__in=exclusively_private,
            media_type=MediaTypes.TV.value,
        ).values_list("id", flat=True)

        if tv_item_ids:
            tv_items = Item.objects.filter(id__in=tv_item_ids)
            related_ids = Item.objects.filter(
                media_id__in=tv_items.values_list("media_id", flat=True),
                source__in=tv_items.values_list("source", flat=True),
                media_type__in=[
                    MediaTypes.SEASON.value,
                    MediaTypes.EPISODE.value,
                ],
            ).values_list("id", flat=True)
            exclusively_private.update(related_ids)

        return list(exclusively_private)

    def get_user_lists_with_item(self, user, item):
        """Return user lists with item membership status."""
        return (
            self.filter(Q(owner=user) | Q(collaborators=user))
            .annotate(
                has_item=models.Exists(
                    CustomListItem.objects.filter(
                        custom_list_id=models.OuterRef("id"),
                        item=item,
                    ),
                ),
            )
            .prefetch_related("collaborators")
            .distinct()
            .order_by("name")
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
    is_public = models.BooleanField(
        default=True,
        help_text="Public lists are visible to all users",
    )

    objects = CustomListManager()

    class Meta:
        """Meta options for the model."""

        ordering = ["name"]

    def __str__(self):
        """Return the name of the custom list."""
        return self.name

    def user_can_view(self, user):
        """Check if the user can view the list."""
        if self.is_public:
            return True
        return self.owner == user or user in self.collaborators.all()

    def user_can_edit(self, user):
        """Check if the user can edit the list."""
        return self.owner == user or user in self.collaborators.all()

    def user_can_delete(self, user):
        """Check if the user can delete the list."""
        return self.owner == user

    def user_is_owner_or_collaborator(self, user):
        """Check if the user is the owner or a collaborator."""
        return self.owner == user or user in self.collaborators.all()

    @property
    def image(self):
        """Return the image of the first item in the list."""
        return self.items.first().image if self.items.first() else settings.IMG_NONE


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
        constraints = [
            models.UniqueConstraint(
                fields=["item", "custom_list"],
                name="%(app_label)s_customlistitem_unique_item_list",
            ),
        ]

    def __str__(self):
        """Return the name of the list item."""
        return self.item.title
