from django.contrib.auth import get_user_model
from django.db.models import Max, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers.common import (
    find_item,
    get_or_create_item_from_metadata,
    image_url,
    media_summary_from_item,
    user_summary,
)
from api.serializers.lists import (
    CollaboratorSerializer,
    CustomListWriteSerializer,
    ListItemsReorderSerializer,
    ListItemWriteSerializer,
)
from api.services.social import set_like
from app.providers import services as provider_services
from lists.models import CustomList, CustomListItem
from social.models import Activity, ContentLike

LIST_PREVIEW_ITEM_LIMIT = 12


def list_payload(custom_list, request=None, *, include_items=False, include_preview_items=False):
    """Serialize a custom list."""
    data = {
        "id": custom_list.id,
        "name": custom_list.name,
        "slug": custom_list.slug,
        "description": custom_list.description,
        "visibility": custom_list.visibility,
        "is_ranked": custom_list.is_ranked,
        "owner": user_summary(custom_list.owner, request=request),
        "collaborators": [
            user_summary(user, request=request) for user in custom_list.collaborators.all()
        ],
        "image_url": image_url(request, custom_list.image),
        "items_count": custom_list.items.count(),
        "updated_at": custom_list.updated_at,
        "like_count": ContentLike.objects.filter(
            target_type=ContentLike.CUSTOM_LIST,
            target_id=custom_list.id,
        ).count(),
    }
    if include_preview_items or include_items:
        items = []
        list_items = custom_list.customlistitem_set.select_related("item").all()
        if include_preview_items and not include_items:
            list_items = list_items[:LIST_PREVIEW_ITEM_LIMIT]
        for list_item in list_items:
            item = media_summary_from_item(
                list_item.item,
                request=request,
                user=request.user,
            )
            item["position"] = list_item.position
            items.append(item)
        if include_preview_items:
            data["preview_items"] = items
        if include_items:
            data["items"] = items
    return data


def _renumber_list_items(custom_list):
    list_items = list(custom_list.customlistitem_set.all())
    for index, list_item in enumerate(list_items, start=1):
        list_item.position = index
    CustomListItem.objects.bulk_update(list_items, ["position"])


def _item_from_ref(ref):
    item = find_item(ref)
    if item is not None:
        return item
    metadata = provider_services.get_media_metadata(
        ref["media_type"],
        ref["media_id"],
        ref["source"],
        [ref.get("season_number")] if ref.get("season_number") is not None else None,
        ref.get("episode_number"),
    )
    return get_or_create_item_from_metadata(ref, metadata)


class ListsView(APIView):
    """List/create custom lists."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        ref_keys = {
            "source": "ref[source]",
            "media_type": "ref[media_type]",
            "media_id": "ref[media_id]",
            "season_number": "ref[season_number]",
            "episode_number": "ref[episode_number]",
        }
        has_ref = any(key in request.query_params for key in ref_keys.values())
        if has_ref:
            serializer = ListItemWriteSerializer(
                data={
                    "ref": {
                        name: request.query_params.get(param)
                        for name, param in ref_keys.items()
                        if request.query_params.get(param) not in (None, "")
                    },
                },
            )
            serializer.is_valid(raise_exception=True)
            lists = CustomList.objects.get_user_lists_with_item(
                request.user,
                _item_from_ref(serializer.validated_data["ref"]),
            )
        else:
            lists = CustomList.objects.get_user_lists(request.user)
        query = request.query_params.get("q", "")
        if query:
            lists = lists.filter(Q(name__icontains=query) | Q(description__icontains=query))
        return Response(
            {
                "count": lists.count(),
                "next": None,
                "previous": None,
                "results": [
                    {
                        **list_payload(custom_list, request=request, include_preview_items=True),
                        **({"has_item": custom_list.has_item} if has_ref else {}),
                    }
                    for custom_list in lists[:100]
                ],
            },
        )

    def post(self, request):
        serializer = CustomListWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        custom_list = CustomList.objects.create(
            owner=request.user,
            name=data["name"],
            slug=data.get("slug", ""),
            description=data.get("description", ""),
            visibility=data.get("visibility", CustomList.Visibility.PRIVATE),
            is_ranked=data.get("is_ranked", False),
        )
        if "collaborator_usernames" in data:
            users = get_user_model().objects.filter(username__in=data["collaborator_usernames"])
            custom_list.collaborators.set(users)
        Activity.objects.create(
            actor=request.user,
            verb="list_created",
            target_type="list",
            target_id=custom_list.id,
            visibility=custom_list.visibility,
            snapshot={"name": custom_list.name},
        )
        return Response(list_payload(custom_list, request=request), status=status.HTTP_201_CREATED)


class ListDetailView(APIView):
    """Read/update/delete a custom list."""

    permission_classes = [IsAuthenticated]

    def get_object(self, request, list_id):
        custom_list = get_object_or_404(
            CustomList.objects.select_related("owner").prefetch_related(
                "collaborators",
                "customlistitem_set__item",
            ),
            id=list_id,
        )
        if custom_list.visibility == CustomList.Visibility.PRIVATE and not custom_list.user_can_view(request.user):
            return None
        return custom_list

    def get(self, request, list_id):
        custom_list = self.get_object(request, list_id)
        if custom_list is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(list_payload(custom_list, request=request, include_items=True))

    def patch(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = CustomListWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        for field in ["name", "slug", "description", "visibility"]:
            if field in data:
                setattr(custom_list, field, data[field])
        if data.get("is_ranked") is True and not custom_list.is_ranked:
            _renumber_list_items(custom_list)
        if "is_ranked" in data:
            custom_list.is_ranked = data["is_ranked"]
        custom_list.save()
        if "collaborator_usernames" in data:
            users = get_user_model().objects.filter(username__in=data["collaborator_usernames"])
            custom_list.collaborators.set(users)
        return Response(list_payload(custom_list, request=request, include_items=True))

    def delete(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_delete(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        custom_list.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListItemsView(APIView):
    """Add an item to a list."""

    permission_classes = [IsAuthenticated]

    def post(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = ListItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = _item_from_ref(serializer.validated_data["ref"])
        defaults = {}
        if custom_list.is_ranked:
            max_position = (
                CustomListItem.objects.filter(custom_list=custom_list).aggregate(Max("position"))["position__max"] or 0
            )
            defaults["position"] = max_position + 1
        _, created = CustomListItem.objects.get_or_create(custom_list=custom_list, item=item, defaults=defaults)
        if created:
            Activity.objects.create(
                actor=request.user,
                verb="list_item_added",
                target_type="list",
                target_id=custom_list.id,
                item=item,
                visibility=custom_list.visibility,
                snapshot={"list_name": custom_list.name},
            )
        return Response({"item": media_summary_from_item(item, request=request, user=request.user)}, status=status.HTTP_201_CREATED)


class ListItemDetailView(APIView):
    """Remove an item from a list."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, list_id, item_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        deleted, _ = CustomListItem.objects.filter(custom_list=custom_list, item_id=item_id).delete()
        if deleted and custom_list.is_ranked:
            _renumber_list_items(custom_list)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListItemsReorderView(APIView):
    """Reorder list items."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = ListItemsReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item_ids = serializer.validated_data["item_ids"]
        current_ids = list(CustomListItem.objects.filter(custom_list=custom_list).values_list("item_id", flat=True))
        if len(item_ids) != len(current_ids) or set(item_ids) != set(current_ids):
            return Response(
                {"item_ids": ["Must include exactly all items currently in the list."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        list_items = {
            list_item.item_id: list_item
            for list_item in CustomListItem.objects.filter(custom_list=custom_list)
        }
        for index, item_id in enumerate(item_ids, start=1):
            list_items[item_id].position = index
        CustomListItem.objects.bulk_update(list_items.values(), ["position"])
        return Response(list_payload(custom_list, request=request, include_items=True))


class ListCollaboratorsView(APIView):
    """Add a collaborator."""

    permission_classes = [IsAuthenticated]

    def post(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id, owner=request.user)
        serializer = CollaboratorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = get_object_or_404(get_user_model(), username=serializer.validated_data["username"])
        custom_list.collaborators.add(user)
        return Response(list_payload(custom_list, request=request, include_items=True))


class ListCollaboratorDetailView(APIView):
    """Remove a collaborator."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, list_id, user_id):
        custom_list = get_object_or_404(CustomList, id=list_id, owner=request.user)
        custom_list.collaborators.remove(user_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListLikeView(APIView):
    """Like/unlike a list."""

    permission_classes = [IsAuthenticated]

    def post(self, request, list_id):
        return Response(set_like(request.user, target_type=ContentLike.CUSTOM_LIST, target_id=list_id, liked=True))

    def delete(self, request, list_id):
        return Response(set_like(request.user, target_type=ContentLike.CUSTOM_LIST, target_id=list_id, liked=False))
