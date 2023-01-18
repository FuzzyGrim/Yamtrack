from app.models import Media, Season
from app.utils import helpers
from django.core.files import File
from django.db.models import Avg, Sum


def add_media(request):
    metadata = request.session.get("metadata")
    media = Media()

    if request.POST["score"] == "":
        media.score = None
    else:
        media.score = float(request.POST["score"])

    if "num_episodes" in metadata and (request.POST["status"] == "Completed" or int(request.POST["progress"]) > metadata["num_episodes"]):
        media.progress = metadata["num_episodes"]
    elif request.POST["progress"] != "":
        media.progress = request.POST["progress"]
    else:
        media.progress = 0

    media.media_id=metadata["id"]
    media.title=metadata["title"]
    media.user=request.user
    media.status=request.POST["status"]
    if "number_of_seasons" in metadata:
        media.num_seasons = metadata["number_of_seasons"]
    else:
        media.num_seasons = 1
    media.media_type = metadata["media_type"]

    media.api = metadata["api"]

    if metadata["image"] == "" or metadata["image"] == None:
        media.image = "images/none.svg"
    else:
        if media.api == "mal":
            img_temp = helpers.get_image_temp(metadata['image'])
            if media.media_type == "anime":
                media.image.save(f"anime-{metadata['image'].rsplit('/', 1)[-1]}", File(img_temp), save=False)
            elif media.media_type == "manga":
                media.image.save(f"manga-{metadata['image'].rsplit('/', 1)[-1]}", File(img_temp), save=False)
            img_temp.close()
        else:        
            img_temp = helpers.get_image_temp(f"https://image.tmdb.org/t/p/w92{metadata['image']}")
            media.image.save(f"tmdb-{metadata['image'].rsplit('/', 1)[-1]}", File(img_temp))
            img_temp.close()

    if "season" in request.POST:
        if request.POST["season"] == "all":
            seasons_list = []
            for season in range(1, media.num_seasons + 1):
                if "episode_count" in metadata["seasons"][season - 1] and media.status == "Completed":
                    seasons_list.append(Season(media=media, title=media.title, number=season, score=media.score, status=media.status, 
                                               progress=metadata["seasons"][season - 1]["episode_count"]))
                else:
                    seasons_list.append(Season(media=media, title=media.title, number=season, score=media.score, status=media.status, progress=0))
            Season.objects.bulk_create(seasons_list)

        else:
            if request.POST["status"] == "Completed" and "episode_count" in metadata["seasons"][int(request.POST["season"]) - 1]:
                media.progress = metadata["seasons"][int(request.POST["season"]) - 1]["episode_count"]
                
            Season.objects.create(media=media, title=media.title, number=request.POST["season"], score=media.score, status=media.status, progress=media.progress)

    else:
        Season.objects.create(media=media, title=media.title, number=1, score=media.score, status=media.status, progress=media.progress)

    media.save()
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
            
            for season in range(1, media.num_seasons + 1):
                if "episode_count" in metadata["seasons"][season - 1] and request.POST["status"] == "Completed":
                    progress = metadata["seasons"][season - 1]["episode_count"]
                    Season.objects.update_or_create(media=media, number=season, 
                                defaults={"title":metadata["title"], "score": score, "status": request.POST["status"], "progress": progress})
                else:
                    obj, created = Season.objects.get_or_create(media=media, number=season, 
                                                                defaults={"title":metadata["title"], 'progress': 0,'score': score, 'status': request.POST["status"]})
                    if not created:
                        Season.objects.filter(media=media, number=season).update(score=score, status=request.POST["status"])                                        
        else:
            if "episode_count" in metadata["seasons"][int(request.POST["season"]) - 1] and (request.POST["status"] == "Completed" or int(request.POST["progress"]) > metadata["seasons"][int(request.POST["season"]) - 1]["episode_count"]):
                progress = metadata["seasons"][int(request.POST["season"]) - 1]["episode_count"]
            elif request.POST["progress"] != "":
                progress = request.POST["progress"]
            else:
                progress = 0
            
            Season.objects.filter(media=media, number=request.POST["season"]).update(score=score, status=request.POST["status"], progress=progress)
            
            # update total number of seasons
            media.num_seasons = metadata["number_of_seasons"]
        
    else:
        if "num_episodes" in metadata and (request.POST["status"] == "Completed" or int(request.POST["progress"]) > metadata["num_episodes"]):
            progress = metadata["num_episodes"]
        elif request.POST["progress"] != "":
            progress = request.POST["progress"]
        else:
            progress = 0

        media.score = score
        media.progress = progress

        Season.objects.filter(media=media, season=1).update(score=score, status=request.POST["status"], progress=progress)

    seasons = Season.objects.filter(media=media)
    mean_score = seasons.aggregate(Avg('score'))['score__avg']
    progress_total = seasons.aggregate(Sum('progress'))['progress__sum']
    media.score = mean_score
    media.progress = progress_total
    media.status = request.POST["status"]
    media.save()

    del request.session["metadata"]