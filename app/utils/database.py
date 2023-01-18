from app.models import Media
from app.utils import helpers
from django.core.files import File


def add_media(request):
    metadata = request.session.get("metadata")
    media = Media()

    if request.POST["score"] == "":
        media.score = None
    else:
        media.score = float(request.POST["score"])

    media.media_id=metadata["id"]
    media.title=metadata["title"]
    media.user=request.user
    media.status=request.POST["status"]
    if "number_of_seasons" in metadata:
        media.num_seasons = metadata["number_of_seasons"]
    else:
        media.num_seasons = 1
    media.media_type = metadata["media_type"]


    if "season" in request.POST:
        if request.POST["season"] == "all":
            for season in range(1, media.num_seasons + 1):
                media.seasons_details[season] = {"score": media.score, "status": media.status, "progress": 0}
        else:
            if request.POST["progress"] != "":
                media.progress = request.POST["progress"]
            elif request.POST["status"] == "Completed" and "episode_count" in metadata["seasons"][int(request.POST["season"])]:
                media.progress = metadata["seasons"][int(request.POST["season"])]["episode_count"]
            else:
                media.progress

            media.seasons_details={request.POST["season"]: {"score":media.score, "status": media.status, "progress": media.progress}}

    else:
        media.seasons_details = {"1": {"score": media.score, "status": media.status, "progress": media.progress}}
        if request.POST["progress"] != "":
            media.progress = request.POST["progress"]
        elif request.POST["status"] == "Completed" and "num_episodes" in metadata:
            media.progress = metadata["num_episodes"]
        else:
            media.progress = 0

    media.api = metadata["api"]

    if metadata["image"] == "" or metadata["image"] == None:
        media.image = "images/none.svg"
        media.save()
    else:
        if media.api == "mal":
            img_temp = helpers.get_image_temp(metadata['image'])
            if media.media_type == "anime":
                media.image.save(f"anime-{metadata['image'].rsplit('/', 1)[-1]}", File(img_temp))
            elif media.media_type == "manga":
                media.image.save(f"manga-{metadata['image'].rsplit('/', 1)[-1]}", File(img_temp))
            img_temp.close()
        else:        
            img_temp = helpers.get_image_temp(f"https://image.tmdb.org/t/p/w92{metadata['image']}")
            media.image.save(f"tmdb-{metadata['image'].rsplit('/', 1)[-1]}", File(img_temp))
            img_temp.close()
    
    del request.session["metadata"]
    

def edit_media(request):
    metadata = request.session.get("metadata")
    if request.POST["score"] == "":
        score = None
    else:
        score = float(request.POST["score"])

    media = Media.objects.get(
        media_id=metadata["id"],
        media_type=metadata["media_type"],
        user=request.user,
        api=metadata["api"],
    )

    if "season" in request.POST:
        if request.POST["season"] == "all":
            for season in range(1, metadata["number_of_seasons"] + 1):
                media.seasons_details[season] = {"score": score, "status": request.POST["status"], "progress": 0}
        else:
            if request.POST["progress"] != "":
                progress = request.POST["progress"]
            elif request.POST["status"] == "Completed" and "episode_count" in metadata["seasons"][int(request.POST["season"])]:
                progress = metadata["seasons"][int(request.POST["season"])]["episode_count"]
            else:
                progress = 0

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

            media.num_seasons = metadata["number_of_seasons"]
    else:
        if request.POST["progress"] != "":
            progress = request.POST["progress"]
        elif request.POST["status"] == "Completed" and "num_episodes" in request.POST:
            progress = request.POST["num_episodes"]
        else:
            progress = 0

        media.score = score
        media.progress = progress

        media.seasons_details = {"1": {"score": score, "status": request.POST["status"], "progress": progress}}
    
    media.status = request.POST["status"]
    media.save()

    del request.session["metadata"]