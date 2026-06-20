from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import FeedCursorPagination
from api.serializers.social import LikeSerializer
from api.services import social as social_service
from social.models import Follow, FollowStatus


class FeedView(APIView):
    """Current user's reverse-chronological feed."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = social_service.feed_queryset(request.user)
        media_type = request.query_params.get("media_type")
        if media_type:
            queryset = queryset.filter(item__media_type=media_type)
        paginator = FeedCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = [
            social_service.activity_payload(activity, request=request, viewer=request.user)
            for activity in page
        ]
        return paginator.get_paginated_response(data)


class UserActivityView(APIView):
    """Activity for a public profile."""

    permission_classes = [AllowAny]

    def get(self, request, username):
        user = get_object_or_404(get_user_model(), username=username)
        queryset = social_service.user_activity_queryset(request.user, user)
        paginator = FeedCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = [
            social_service.activity_payload(activity, request=request, viewer=request.user)
            for activity in page
        ]
        return paginator.get_paginated_response(data)


class FollowView(APIView):
    """Follow/unfollow a user."""

    permission_classes = [IsAuthenticated]

    def post(self, request, username):
        follow = social_service.follow_user(request.user, username)
        return Response({"state": "requested" if follow.status == FollowStatus.PENDING else "following"})

    def delete(self, request, username):
        social_service.unfollow_user(request.user, username)
        return Response({"state": "none"})


class FollowRequestsView(APIView):
    """Inbound follow requests."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        requests = Follow.objects.filter(to_user=request.user, status=FollowStatus.PENDING).select_related("from_user")
        return Response(
            {
                "results": [
                    {
                        "id": follow.id,
                        "from_user": {
                            "id": follow.from_user.id,
                            "username": follow.from_user.username,
                            "display_name": follow.from_user.display_name or follow.from_user.username,
                        },
                        "created_at": follow.created_at,
                    }
                    for follow in requests
                ],
            },
        )


class FollowRequestActionView(APIView):
    """Accept or reject a follow request."""

    permission_classes = [IsAuthenticated]

    def post(self, request, request_id, action):
        follow = get_object_or_404(Follow, id=request_id, to_user=request.user, status=FollowStatus.PENDING)
        if action == "accept":
            follow.status = FollowStatus.ACCEPTED
            follow.save(update_fields=["status", "updated_at"])
            return Response({"state": "following"})
        if action == "reject":
            follow.delete()
            return Response({"state": "rejected"})
        return Response(status=status.HTTP_404_NOT_FOUND)


class BlockView(APIView):
    """Block/unblock a user."""

    permission_classes = [IsAuthenticated]

    def post(self, request, username):
        social_service.block_user(request.user, username)
        return Response({"blocked": True})

    def delete(self, request, username):
        social_service.unblock_user(request.user, username)
        return Response({"blocked": False})


class GenericLikeView(APIView):
    """Generic like endpoint."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LikeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(social_service.set_like(request.user, liked=True, **serializer.validated_data))

    def delete(self, request):
        serializer = LikeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(social_service.set_like(request.user, liked=False, **serializer.validated_data))
