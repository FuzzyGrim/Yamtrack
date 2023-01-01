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
        if request.POST["season"] == "all":
            for season in range(1, int(request.POST["num_seasons"]) + 1):
                media.seasons_details[season] = {"score": media.score, "status": request.POST["status"]}

        else:
            media.seasons_details={request.POST["season"]: {"score":media.score, "status":request.POST["status"]}}

            if request.POST["season"] != request.POST["num_seasons"] and media.status == "Completed":
                media.status = "Watching"

    media.api_origin = request.POST["api_origin"]
    
    if media.api_origin == "mal":
        media.media_type = request.POST["media_type"]
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
        score = None
    else:
        score = float(request.POST["score"])

    media = Media.objects.get(
        media_id=request.POST["media_id"],
        media_type=request.POST["media_type"],
        user=request.user,
        api_origin=request.POST["api_origin"],
    )

    media.score = score
    media.status = request.POST["status"]
    
    if "season" in request.POST:
        if request.POST["season"] == "all":
            for season in range(1, int(request.POST["num_seasons"]) + 1):
                media.seasons_details[season] = {"score": request.POST["score"], "status": request.POST["status"]}
        else:
            # if media didn't have seasons before, add the previous score as season 1
            if not media.seasons_details:
                media.seasons_details = {"1": {"score": media.score, "status": media.status}}
            media.seasons_details[request.POST["season"]] = {"score": request.POST["score"], "status": request.POST["status"]}
            dict(sorted(media.seasons_details.items()))

            # calculate the average score
            total = 0
            valued_seasons = 0
            for season in media.seasons_details:
                if media.seasons_details[season]["score"] is not None:
                    total += float(media.seasons_details[season]["score"])
                    valued_seasons += 1
            if valued_seasons != 0:
                media.score = round(total / valued_seasons, 1)
            
            # calculate the status
            if request.POST["season"] != request.POST["num_seasons"] and media.status == "Completed":
                media.status = "Watching"

            media.num_seasons = request.POST["num_seasons"]

    media.save()