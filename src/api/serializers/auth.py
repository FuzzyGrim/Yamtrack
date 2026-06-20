from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from api.serializers.profile import profile_payload


class RegisterSerializer(serializers.Serializer):
    """Register a user and return JWT tokens."""

    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": ["Passwords do not match."]})
        return attrs

    def create(self, validated_data):
        User = get_user_model()
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data.get("email", ""),
            password=validated_data["password"],
        )


class LoginSerializer(serializers.Serializer):
    """Authenticate with username/email and password."""

    username_or_email = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        username_or_email = attrs["username_or_email"]
        password = attrs["password"]
        user = authenticate(
            request=self.context.get("request"),
            username=username_or_email,
            password=password,
        )
        if user is None:
            User = get_user_model()
            username = (
                User.objects.filter(email__iexact=username_or_email)
                .values_list("username", flat=True)
                .first()
            )
            if username:
                user = authenticate(
                    request=self.context.get("request"),
                    username=username,
                    password=password,
                )
        if user is None:
            raise serializers.ValidationError("Invalid username/email or password.")
        if not user.is_active:
            raise serializers.ValidationError("This account is inactive.")
        attrs["user"] = user
        return attrs


def token_response(user, request=None):
    """Return token pair and serialized user."""
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": profile_payload(user, request=request, viewer=user),
    }
