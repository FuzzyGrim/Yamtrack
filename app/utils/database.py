from app.models import Media
from app.utils import helpers
from django.core.files import File

def add_media(request):
    media = Media()

    if request.POST["score"] == "":
        media.score = None
    else:
        media.score = float(request.POST["score"])

    media.media_id=request.POST["media_id"]
    media.title=request.POST["title"]
    media.user=request.user
    media.status=request.POST["status"]
    media.num_seasons=request.POST.get("num_seasons")

    if "season" in request.POST:
        media.seasons_score={request.POST["season"]: request.POST["score"]}

    media.api_origin = request.POST["api_origin"]
    
    if media.api_origin == "mal":
        media.media_type = helpers.convert_mal_media_type(request.POST["media_type"])
        img_temp = helpers.get_image_temp(request.POST["image"])
    else:
        media.media_type = request.POST["media_type"]
        img_temp = helpers.get_image_temp(f"https://image.tmdb.org/t/p/w92{request.POST['image']}")

    if request.POST['image'] == "" or request.POST['image'] == None:
        media.image.save(f"none.svg", File(img_temp))
    else:
        if media.media_type == "anime":
            media.image.save(f"anime-{request.POST['image'].rsplit('/', 1)[-1]}", File(img_temp))
        
        elif media.media_type == "manga":
            media.image.save(f"manga-{request.POST['image'].rsplit('/', 1)[-1]}", File(img_temp))
        
        else:
            media.image.save(f"tmdb-{request.POST['image'].rsplit('/', 1)[-1]}", File(img_temp))
    
    
def edit_media(request):
    if request.POST["score"] == "":
        request.POST["score"] = None
    else:
        score = float(request.POST["score"])

    media = Media.objects.get(
        media_id=request.POST["media_id"],
        user=request.user,
        api_origin=request.POST["api_origin"],
    )
    
    if "season" in request.POST:
        # if media didn't have seasons before, add the previous score as season 1
        if not media.seasons_score:
            media.seasons_score["1"] = media.score
        media.seasons_score[request.POST["season"]] = score
        dict(sorted(media.seasons_score.items()))

        # calculate the average score
        total = 0
        valued_seasons = 0
        for season in media.seasons_score:
            if media.seasons_score[season] is not None:
                total += media.seasons_score[season]
                valued_seasons += 1
        if valued_seasons != 0:
            media.score = round(total / valued_seasons, 1)

        media.num_seasons = request.POST["num_seasons"]

    else:
        media.score = score

    media.status = request.POST["status"]

    media.save()