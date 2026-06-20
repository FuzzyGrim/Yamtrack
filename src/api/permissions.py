from rest_framework.permissions import SAFE_METHODS, BasePermission

from social.models import Block, Follow, FollowStatus


def users_blocked(user, other_user):
    """Return whether either user blocks the other."""
    if not user or not user.is_authenticated or not other_user:
        return False
    return Block.objects.filter(
        blocker__in=[user, other_user],
        blocked__in=[user, other_user],
    ).exists()


def can_view_user_profile(viewer, target_user):
    """Check public/private profile visibility and block rules."""
    if target_user is None:
        return False
    if viewer.is_authenticated and viewer == target_user:
        return True
    if viewer.is_authenticated and users_blocked(viewer, target_user):
        return False
    if not target_user.profile_private:
        return True
    if not viewer.is_authenticated:
        return False
    return Follow.objects.filter(
        from_user=viewer,
        to_user=target_user,
        status=FollowStatus.ACCEPTED,
    ).exists()


class IsOwnerOrReadOnly(BasePermission):
    """Allow owners to mutate objects and authenticated users to read."""

    owner_attr = "user"

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return getattr(obj, self.owner_attr, None) == request.user


class IsListEditorOrReadOnly(BasePermission):
    """Allow list owners/collaborators to edit."""

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return obj.user_can_edit(request.user)
