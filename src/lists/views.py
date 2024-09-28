import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from app import helpers
from app.models import Item
from lists.forms import CustomListForm, FilterListItemsForm
from lists.models import CustomList, CustomListItem

logger = logging.getLogger(__name__)


@require_GET
def lists(request):
    """Return the custom list page."""
    custom_lists = CustomList.objects.get_user_lists(request.user)

    # Create a form for each list
    # needs unique id for django-select2
    for i, custom_list in enumerate(custom_lists, start=1):
        custom_list.form = CustomListForm(
            instance=custom_list,
            auto_id=f"id_{i}_%s",
        )

    create_list_form = CustomListForm()

    return render(
        request,
        "lists/custom_lists.html",
        {"custom_lists": custom_lists, "form": create_list_form},
    )


@require_GET
def list_detail(request, list_id):
    """Return the detail page of a custom list."""
    custom_list = get_object_or_404(CustomList, id=list_id)
    if not custom_list.user_can_view(request.user):
        messages.error(request, "You do not have permission to view this list.")
        return helpers.redirect_back(request)

    form = CustomListForm(instance=custom_list)
    items = custom_list.items.all()

    if request.GET:
        filter_form = FilterListItemsForm(request.GET)
        if filter_form.is_valid():
            media_type = filter_form.cleaned_data["media_type"]
            sort = filter_form.cleaned_data["sort"]

            # Apply media type filter
            if media_type != "all":
                items = items.filter(media_type=media_type)

            # Apply sorting
            if sort == "title":
                items = items.order_by("title")
    else:
        filter_form = FilterListItemsForm()

    last_added_date = CustomListItem.objects.get_last_added_date(custom_list)

    return render(
        request,
        "lists/list_detail.html",
        {
            "custom_list": custom_list,
            "form": form,
            "filter_form": filter_form,
            "items": items,
            "last_added_date": last_added_date,
        },
    )


@require_POST
def create(request):
    """Create a new custom list."""
    form = CustomListForm(request.POST, owner=request.user)
    if form.is_valid():
        custom_list = form.save(commit=False)
        custom_list.owner = request.user
        custom_list.save()
        logger.info("%s list created successfully.", custom_list)
    else:
        messages.error(request, "There was an error creating the list.")
    return helpers.redirect_back(request)


@require_POST
def edit(request):
    """Edit an existing custom list."""
    list_id = request.POST.get("list_id")
    custom_list = get_object_or_404(CustomList, id=list_id)
    if custom_list.user_can_edit(request.user):
        form = CustomListForm(request.POST, instance=custom_list)
        if form.is_valid():
            form.save()
            logger.info("%s list edited successfully.", custom_list)
    else:
        messages.error(request, "You do not have permission to edit this list.")
    return helpers.redirect_back(request)


@require_POST
def delete(request):
    """Delete a custom list."""
    list_id = request.POST.get("list_id")
    custom_list = get_object_or_404(CustomList, id=list_id)
    if custom_list.user_can_delete(request.user):
        custom_list.delete()
        logger.info("%s list deleted successfully.", custom_list)
    else:
        messages.error(request, "You do not have permission to delete this list.")
    return helpers.redirect_back(request)


@require_GET
def lists_modal(request):
    """Return the modal showing all custom lists and allowing to add to them."""
    media_type = request.GET["media_type"]
    source = request.GET["source"]
    media_id = request.GET["media_id"]
    season_number = request.GET.get("season_number")
    episode_number = request.GET.get("episode_number")

    item, _ = Item.objects.get_or_create(
        media_id=media_id,
        source=source,
        media_type=media_type,
        season_number=season_number,
        episode_number=episode_number,
        defaults={
            "title": request.GET["title"],
            "image": request.GET["image"],
        },
    )

    custom_lists = CustomList.objects.filter(owner=request.user)

    return render(
        request,
        "lists/components/fill_lists.html",
        {"item": item, "custom_lists": custom_lists},
    )


@require_POST
def list_item_toggle(request):
    """Add or remove an item from a custom list."""
    item_id = request.POST["item_id"]
    custom_list_id = request.POST["custom_list_id"]

    item = get_object_or_404(Item, id=item_id)
    custom_list = get_object_or_404(CustomList, id=custom_list_id, owner=request.user)

    if item in custom_list.items.all():
        custom_list.items.remove(item)
        logger.info("%s removed from %s.", item, custom_list)
        icon_class = "bi bi-plus-square me-1"
    else:
        custom_list.items.add(item)
        logger.info("%s added to %s.", item, custom_list)
        icon_class = "bi bi-check-square-fill me-1"

    return render(
        request,
        "lists/components/list_icon.html",
        {"icon_class": icon_class},
    )
