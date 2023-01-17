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
    media.media_type = request.POST["media_type"]

    if request.POST["progress"] != "":
        media.progress = request.POST["progress"]
    elif request.POST["status"] == "Completed" and "total-episodes" in request.POST:
        media.progress = request.POST["total-episodes"]

    if "season" in request.POST:
        if request.POST["season"] == "all":
            for season in range(1, int(request.POST["num_seasons"]) + 1):
                media.seasons_details[season] = {"score": media.score, "status": media.status, "progress": 0}
        else:
            media.seasons_details={request.POST["season"]: {"score":media.score, "status": media.status, "progress": media.progress}}

            if request.POST["season"] != request.POST["num_seasons"] and media.status == "Completed":
                media.status = "Watching"
    else:
        media.seasons_details = {"1": {"score": media.score, "status": media.status, "progress": media.progress}}

    media.api = request.POST["api"]

    if request.POST['image'] == "" or request.POST['image'] == None:
        media.image = "images/none.svg"
        media.save()
    else:
        if media.api == "mal":
            img_temp = helpers.get_image_temp(request.POST["image"])
            if media.media_type == "anime":
                media.image.save(f"anime-{request.POST['image'].rsplit('/', 1)[-1]}", File(img_temp))
            elif media.media_type == "manga":
                media.image.save(f"manga-{request.POST['image'].rsplit('/', 1)[-1]}", File(img_temp))
            img_temp.close()
        else:        
            img_temp = helpers.get_image_temp(f"https://image.tmdb.org/t/p/w92{request.POST['image']}")
            media.image.save(f"tmdb-{request.POST['image'].rsplit('/', 1)[-1]}", File(img_temp))
            img_temp.close()
    

def edit_media(request):
    if request.POST["score"] == "":
        score = None
    else:
        score = float(request.POST["score"])

    media = Media.objects.get(
        media_id=request.POST["media_id"],
        media_type=request.POST["media_type"],
        user=request.user,
        api=request.POST["api"],
    )

    if request.POST["progress"] != "":
        progress = request.POST["progress"]
    elif request.POST["status"] == "Completed" and "total-episodes" in request.POST:
        progress = request.POST["total-episodes"]
    else:
        progress = None

    if "season" in request.POST:
        if request.POST["season"] == "all":
            for season in range(1, int(request.POST["num_seasons"]) + 1):
                media.seasons_details[season] = {"score": score, "status": request.POST["status"], "progress": None}
        else:
            media.seasons_details[request.POST["season"]] = {"score": score, "status": request.POST["status"], "progress": progress}
            dict(sorted(media.seasons_details.items()))

            # update the average score and update the general progress
            score_total = 0
            scored_seasons = 0
            progress_total = 0
            for season in media.seasons_details:
                if media.seasons_details[season]["score"] is not None:
                    score_total += float(media.seasons_details[season]["score"])
                    scored_seasons += 1
                progress_total += int(media.seasons_details[season]["progress"])
            
            media.progress = progress_total

            if scored_seasons != 0:
                media.score = round(score_total / scored_seasons, 1)            

            media.num_seasons = request.POST["num_seasons"]
    else:
        media.score = score
        media.progress = progress

        media.seasons_details = {"1": {"score": score, "status": request.POST["status"], "progress": progress}}
    
    media.status = request.POST["status"]
    media.save()