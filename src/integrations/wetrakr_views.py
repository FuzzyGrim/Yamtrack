"""Connect a WeTrakr account and choose what Floppy sends to it, and when."""

import uuid
from datetime import timedelta

from cryptography.fernet import InvalidToken
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from integrations import wetrakr_api
from integrations.imports.helpers import decrypt, encrypt
from integrations.models import (
    CAPABILITY_PLAY_PUSH,
    CAPABILITY_PLAY_REMOVE,
    CAPABILITY_RATING_PUSH,
    CAPABILITY_STATUS_PUSH,
    OutboundDeliveryStatus,
    SyncBinding,
    SyncBindingStatus,
    SyncClientKind,
    SyncDeliverySchedule,
    SyncDirection,
    WeTrakrAccount,
)
from integrations.state import outbound
from integrations.state.adapters.base import AdapterError
from integrations.state.identity import (
    activate_binding,
    deactivate_binding,
    get_or_create_binding,
)

INSTANCE_KEY = "wetrakr.com"
# What the card lets the user switch on, by form field.
SENDABLE = (
    ("plays", CAPABILITY_PLAY_PUSH),
    ("removals", CAPABILITY_PLAY_REMOVE),
    ("statuses", CAPABILITY_STATUS_PUSH),
    ("ratings", CAPABILITY_RATING_PUSH),
)
MIN_POLL_SECONDS = 3


def _binding_for(account):
    if account is None or not account.profile_key:
        return None
    return SyncBinding.objects.filter(
        user=account.user,
        client_kind=SyncClientKind.WETRAKR.value,
        profile_key=account.profile_key,
    ).first()


def wetrakr_status(user):
    """Return bounded connection and delivery state for the settings card."""
    from integrations.tasks._wetrakr_export import library_preview

    account = WeTrakrAccount.objects.filter(user=user).first()
    binding = _binding_for(account) if account and account.is_connected else None
    return {
        "account": account,
        "has_key": bool(wetrakr_api.client_id_for(user)),
        "binding": binding,
        "enabled": {
            name: bool(binding and binding.allows(SyncDirection.OUTBOUND.value, cap))
            for name, cap in SENDABLE
        },
        "any_enabled": bool(
            binding
            and any(
                binding.allows(SyncDirection.OUTBOUND.value, cap)
                for name, cap in SENDABLE
                if name != "removals"
            ),
        ),
        "schedules": SyncDeliverySchedule.choices,
        "counts": dict(
            binding.deliveries.values_list("status").annotate(total=Count("pk")),
        )
        if binding
        else {},
        "recent": binding.deliveries.select_related("item").order_by("-created_at")[:10]
        if binding
        else [],
        "preview": library_preview(user) if binding else {},
    }


def _stop_bindings(user):
    """Withdraw approval and retire waiting writes before changing accounts."""
    for binding in SyncBinding.objects.filter(
        user=user,
        client_kind=SyncClientKind.WETRAKR.value,
    ):
        deactivate_binding(binding)
        binding.deliveries.filter(status=OutboundDeliveryStatus.PENDING.value).update(
            status=OutboundDeliveryStatus.SKIPPED.value,
        )


@login_required
@require_POST
def wetrakr_connect(request):
    """Ask WeTrakr for a login code the user approves on wetrakr.com."""
    client_id = wetrakr_api.client_id_for(request.user)
    if not client_id:
        messages.error(
            request,
            "This instance has no WeTrakr app key, so WeTrakr cannot be connected.",
        )
        return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=connection")
    existing = WeTrakrAccount.objects.filter(user=request.user).first()
    if existing is not None and existing.is_connected:
        messages.error(request, "WeTrakr is already connected.")
        return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=connection")
    try:
        flow = wetrakr_api.start_device_flow(client_id)
    except wetrakr_api.DeviceFlowError as error:
        messages.error(request, str(error))
        return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=connection")
    except AdapterError as error:
        messages.error(request, str(error))
        return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=connection")
    WeTrakrAccount.objects.update_or_create(
        user=request.user,
        defaults={
            "profile_key": existing.profile_key if existing else uuid.uuid4().hex,
            "client_id": client_id,
            "device_code": encrypt(flow["device_code"]),
            "user_code": flow["user_code"],
            "verification_url": flow["verification_url"],
            "device_interval": max(flow["interval"], MIN_POLL_SECONDS),
            "device_expires_at": timezone.now() + timedelta(seconds=flow["expires_in"]),
        },
    )
    return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=connection")


def _clear_device_flow(account):
    """Forget a finished approval; drop the row if nothing was ever connected."""
    if account.refresh_token:
        WeTrakrAccount.objects.filter(pk=account.pk).update(
            device_code="",
            user_code="",
            verification_url="",
            device_expires_at=None,
        )
    else:
        account.delete()


def _finish_connection(user, account, tokens):
    """Store the approved login and create the (still switched off) binding."""
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        _stop_bindings(user)
        profile_key = uuid.uuid4().hex
        WeTrakrAccount.objects.filter(pk=account.pk).update(
            profile_key=profile_key,
            device_code="",
            user_code="",
            verification_url="",
            device_expires_at=None,
            connection_broken=False,
            last_error_message="",
            last_failed_at=None,
            failure_count=0,
            **wetrakr_api.token_fields(tokens),
        )
        binding, _ = get_or_create_binding(
            user,
            SyncClientKind.WETRAKR.value,
            instance_key=INSTANCE_KEY,
            profile_key=profile_key,
            label="WeTrakr",
        )
        binding.kill_switch = False
        binding.save(update_fields=["kill_switch", "updated_at"])


@login_required
@require_POST
def wetrakr_poll(request):
    """Check once whether the user has approved; the card calls this repeatedly."""
    account = WeTrakrAccount.objects.filter(user=request.user).first()
    if account is None or not account.is_pending:
        return JsonResponse({"state": "idle"})
    if account.device_expires_at and account.device_expires_at <= timezone.now():
        _clear_device_flow(account)
        messages.error(request, "The code expired. Start again.")
        return JsonResponse({"state": "failed"})
    pace = max(account.device_interval, MIN_POLL_SECONDS)
    if not cache.add(f"wetrakr_poll:{request.user.pk}", 1, timeout=pace - 1):
        return JsonResponse({"state": "pending", "interval": pace})
    try:
        state, payload = wetrakr_api.poll_device_flow(
            account.client_id,
            decrypt(account.device_code),
        )
    except AdapterError:
        return JsonResponse({"state": "pending", "interval": pace})
    if state == "approved":
        _finish_connection(request.user, account, payload)
        messages.success(
            request,
            "WeTrakr connected. Choose what Floppy sends to it.",
        )
        return JsonResponse({"state": "approved"})
    if state == "failed":
        _clear_device_flow(account)
        messages.error(request, payload)
        return JsonResponse({"state": "failed"})
    if payload and payload.get("interval"):
        pace = max(payload["interval"], pace)
        WeTrakrAccount.objects.filter(pk=account.pk).update(device_interval=pace)
    return JsonResponse({"state": "pending", "interval": pace})


@login_required
@require_POST
def wetrakr_cancel(request):
    """Abandon a login code that has not been approved."""
    account = WeTrakrAccount.objects.filter(user=request.user).first()
    if account is not None and account.is_pending:
        _clear_device_flow(account)
    return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=connection")


@login_required
@require_POST
def wetrakr_settings(request):
    """Choose what is sent, and how soon. Nothing is sent that is switched off."""
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        account = WeTrakrAccount.objects.filter(user=request.user).first()
        binding = _binding_for(account) if account and account.is_connected else None
        if binding is None:
            messages.error(request, "Connect your WeTrakr account first.")
            return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=sync")
        capabilities = [
            capability
            for name, capability in SENDABLE
            if request.POST.get(name) == "on"
        ]
        if capabilities:
            activate_binding(
                binding,
                capabilities=capabilities,
                directions=[SyncDirection.OUTBOUND.value],
            )
        else:
            _stop_bindings(request.user)
        schedule = request.POST.get("schedule", "")
        if schedule in SyncDeliverySchedule.values:
            outbound.set_delivery_schedule(binding, schedule)
    messages.success(request, "Updated WeTrakr sync.")
    return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=sync")


@login_required
@require_POST
def wetrakr_disconnect(request):
    """Stop locally first, then ask WeTrakr to end the login."""
    login = None
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        _stop_bindings(request.user)
        account = WeTrakrAccount.objects.filter(user=request.user).first()
        if account is not None and account.refresh_token:
            try:
                login = (
                    account.client_id,
                    decrypt(account.access_token),
                    decrypt(account.refresh_token),
                )
            except InvalidToken:
                login = None
        WeTrakrAccount.objects.filter(user=request.user).delete()
    if login is not None:
        try:
            wetrakr_api.logout(*login)
        except AdapterError:
            messages.warning(
                request,
                "Disconnected locally. WeTrakr could not confirm the logout.",
            )
    messages.success(request, "WeTrakr disconnected.")
    return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=connection")


@login_required
@require_POST
def wetrakr_export(request):
    """Queue the user's existing library, only because they asked for it."""
    from integrations.tasks import export_wetrakr_library

    if not SyncBinding.objects.filter(
        user=request.user,
        client_kind=SyncClientKind.WETRAKR.value,
        status=SyncBindingStatus.ACTIVE.value,
        kill_switch=False,
    ).exists():
        messages.error(request, "Switch on WeTrakr sync before sending your library.")
        return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=sync")
    export_wetrakr_library.delay(request.user.pk)
    messages.success(
        request,
        "Sending queued. Results will appear under WeTrakr.",
    )
    return redirect(f"{reverse('provider_detail', args=['wetrakr'])}?tab=sync")
