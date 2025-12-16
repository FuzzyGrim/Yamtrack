import datetime
import logging

import requests
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache

from app.models import MediaTypes, Sources
from app.providers import igdb, services

logger = logging.getLogger(__name__)
base_url = "https://api.isthereanydeal.com"
apikey = getattr(settings, "ITAD_API_KEY", None)
country_code = getattr(settings, "ITAD_COUNTRY_CODE", "US")

range_texts = {
    "all": "All time",
    "y1": "1 year",
    "m3": "3 months",
}

STEAM_SHOP_ID = 61

"""Icons are from https://www.svgrepo.com"""
drm_icons = {
    "Steam": "steam",
    "GOG": "gog",
    "Epic": "epic-games",
    "EA App": "ea",
    "Ubisoft Store": "ubisoft",
    "Microsoft Store": "ms-store",
    "Drm Free": "lock-keyhole-unlocked",
}

price_cache_key = f"{Sources.ITAD.value}_{MediaTypes.GAME.value}_prices_"

class ExternalGamePriceSource:
    """External game price source from IsThereAnyDeal (ITAD) API."""

def handle_error(error, request):
    """Handle ITAD API errors."""
    msg = "Error while getting prices from ITAD, check the logs for more information."
    if request is not None:
        messages.error(request, msg)
    logger.error("%s error: %s", "ITAD", error.response.text)

def check_if_enabled():
    """
    Check if we want to run the API calls.

    Returns:
        True if API key is set, otherwise False
    """
    return bool(apikey)

def lookup(media_id, request):
    """Search for ITAD game ID from steam appid.

    Args:
        media_id (int): The id of the game in IGDB
        steam_appid (int): The ID of the game on Steam
        request: The http request we are working with

    Returns:
        The ID of the game in ITAD's system if found, otherwise None
    """
    cache_key = f"{Sources.ITAD.value}_{MediaTypes.GAME.value}_steamappid_{media_id}"
    itad_appid = cache.get(cache_key)

    if itad_appid is None:
        try:
            steam_appid = igdb.external_game_id(media_id)
            if steam_appid is not None:
                url = f"{base_url}/games/lookup/v1?appid={steam_appid}&key={apikey}"
                response = services.api_request(
                    Sources.ITAD,
                    "GET",
                    url,
                )

                if response is not None and response["found"]:
                    itad_appid = response["game"]["id"]
                    cache.set(cache_key, itad_appid)
        except requests.exceptions.HTTPError as error:
            handle_error(error, request)

    return itad_appid

def prices(media_id, media_metadata = None, request = None, notify_success = False):  # noqa: FBT002
    """Get the current prices of the game and add them to the game's metadata.

    Args:
        media_id(int): The ID of the game in IGDB's system
        media_metadata: The metadata of the game
        request: The http request we are working with
        notify_success(bool): True if notification should be sent
    """
    if check_if_enabled():
        cache_key = f"{price_cache_key}{media_id}"
        prices = cache.get(cache_key)
        itad_appid = lookup(media_id, request)
        if prices is None and itad_appid is not None:
            prices = get_current_prices(media_id, itad_appid, request)
            if request and notify_success:
                msg = "Prices from ITAD was synced successfully."
                messages.success(request, msg)
        if media_metadata and prices and itad_appid:
            enrich_items_with_prices(media_metadata, prices, itad_appid)


def get_current_prices(media_id, itad_appid, request):
    """Get the current prices for the specified game.

    Args:
        media_id (int): The id of the game in IGDB
        itad_appid (str): The ID of the game in ITAD
        request: The http request we are working with

    Returns:
        The current prices of the game
    """
    cache_key = f"{price_cache_key}{media_id}"
    prices = cache.get(cache_key)

    if prices is None:
        try:
            url = f"{base_url}/games/prices/v3?key={apikey}&country={country_code}"

            data = f'[ "{itad_appid}" ]'
            response = services.api_request(
                Sources.ITAD,
                "POST",
                url,
                data = data,
            )

            if response and len(response) > 0:
                prices = response[0] if response and len(response) > 0 else None

        except requests.exceptions.HTTPError as error:
            handle_error(error, request)

        cache.set(cache_key, prices)
    return prices

def enrich_items_with_prices(media_metadata, price_data, itad_appid):
    """Enrich the game metadata with the prices.

    Args:
        media_metadata: The metadata of the game
        price_data: The price data of the game
        itad_appid: The id of the game on ITAD
    """
    if price_data is not None:
        data = {}
        data["itad_appid"] = itad_appid
        data["data"] = { "lowest prices": None, "deals": None }
        lowprices = []
        for key, deal in price_data["historyLow"].items():
            info = {
                "type": "lows",
                "price":
                {
                    "text": range_texts[key],
                    "amount": deal.get("amount", "-") if deal else "-",
                    "currency": deal.get("currency", "") if deal else "",
                },
            }
            lowprices.append(info)
        data["data"]["lowest prices"] = lowprices

        deals = []

        for deal in price_data["deals"]:
            if deal.get("drm", None) and len(deal["drm"]) > 0:
                drm = [drm_icons[drm_item["name"]] for drm_item in deal["drm"]]
            elif deal["shop"]["id"] == STEAM_SHOP_ID:
                drm = [drm_icons["Steam"]]
            else:
                drm = None

            if deal.get("expiry", None):
                expiry = remaing_time(datetime.datetime.fromisoformat(deal["expiry"]))
            else:
                expiry = ""
            info = {
                "type": "deal",
                "deal":
                    {
                        "shop": deal["shop"]["name"],
                        "currency": deal["price"]["currency"],
                        "regular": deal["regular"]["amount"],
                        "price": deal["price"]["amount"],
                        "url": deal["url"],
                        "cut": deal["cut"],
                        "drm": drm,
                        "expiry": expiry,
                    },
                }
            deals.append(info)

        data["data"]["deals"] = deals

        media_metadata["prices"] = data

def remaing_time(expiry):
    """Format the remaining date from now.

    Args:
        expiry: The date until the deal is up

    Returns:
        The formatted remaining time text
    """
    delta = expiry - datetime.datetime.now(datetime.UTC)
    d = {"days": delta.days}
    d["hours"], rem = divmod(delta.seconds, 3600)
    d["minutes"], d["seconds"] = divmod(rem, 60)
    return "{days} days {hours:02d}:{minutes:02d}".format(**d)
