from decouple import config
import requests

API_KEY = config("API")


def search(content, query):
    url = "https://api.themoviedb.org/3/search/{}?api_key={}&query={}&include_adult=true".format(content, API_KEY, query)
    response = requests.get(url).json()

    if content == "tv":
        for result in response["results"]:
            result["seasons"] = seasons(result["id"])
    elif content == "multi":
        for result in response["results"]:
            if result["media_type"] == "tv":
                result["seasons"] = seasons(result["id"])

    return response


def seasons(id):

    url = "https://api.themoviedb.org/3/tv/{}?api_key={}".format(str(id), API_KEY)
    
    return requests.get(url).json()["last_episode_to_air"]["season_number"]