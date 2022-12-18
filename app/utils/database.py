from app.models import Media

def add_media(request):
    if request.POST["score"] == "":
        request.POST["score"] = None
    else:
        score = float(request.POST["score"])

    media = Media(
        media_id=request.POST["media_id"],
        title=request.POST["title"],
        image=request.POST["image"],
        media_type=request.POST["media_type"],
        score=score,
        user=request.user,
        status=request.POST["status"],
        num_seasons=request.POST.get("num_seasons"),
        api_origin=request.POST["api_origin"],
    )

    if "season" in request.POST:
        media.seasons_score={request.POST["season"]: request.POST["score"]}

    media.save()
    
    
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
    if request.POST["season"]:
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