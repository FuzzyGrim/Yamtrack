from contextlib import suppress

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_decode
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from api.serializers.auth import LoginSerializer, RegisterSerializer, token_response
from api.throttling import AuthRateThrottle


class RegisterView(APIView):
    """Create an account and return tokens."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(token_response(user, request=request), status=status.HTTP_201_CREATED)


class LoginView(APIView):
    """Login with username/email and password."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(token_response(serializer.validated_data["user"], request=request))


class RefreshView(APIView):
    """Refresh JWT tokens."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


class LogoutView(APIView):
    """Blacklist a refresh token."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        with suppress(KeyError, TokenError):
            RefreshToken(request.data["refresh"]).blacklist()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetView(APIView):
    """Send a password reset email using Django's built-in form."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        form = PasswordResetForm(data=request.data)
        form.is_valid()
        form.save(request=request)
        return Response({"detail": "If an account exists, a reset email has been sent."})


class PasswordResetConfirmView(APIView):
    """Confirm a password reset from mobile deep-link params."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        User = get_user_model()
        uid = request.data.get("uid")
        token = request.data.get("token")
        user = None
        try:
            user = User.objects.get(pk=urlsafe_base64_decode(uid).decode())
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None
        if user is None or not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "Invalid or expired reset token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        form = SetPasswordForm(user=user, data={"new_password1": request.data.get("new_password"), "new_password2": request.data.get("new_password")})
        form.is_valid()
        if form.errors:
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        form.save()
        return Response({"detail": "Password reset complete."})


class AppleAuthView(APIView):
    """Phase 2 hook for Sign in with Apple."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        return Response(
            {"detail": "Sign in with Apple is planned for a later API slice."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
