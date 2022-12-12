from django.shortcuts import render, redirect
from app.utils import api

def index(request):
    """Home page"""
    if ("content" and "query") in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )

    return render(request, "app/index.html")

def search(request, content, query):
    """Search page"""
    if ("content" and "query") in request.POST:
        return redirect(
            "/search/" + request.POST["content"] + "/" + request.POST["query"] + "/"
        )
    
    context = {
        "query_list": api.search(content, query)
    }

    return render(request, "app/search.html", context)
    