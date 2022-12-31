document.addEventListener("DOMContentLoaded", function() {
    var buttons = document.getElementsByClassName("open-modal-button");

    for (var i = 0; i < buttons.length; i++) {
        buttons[i].onclick = function() {
            var url = this.getAttribute("data-url");
            var parts = url.split('/');
            var type = parts[2];
            var id = parts[3];
            
            var xhr = new XMLHttpRequest();
            xhr.open('GET', url);
            xhr.onload = function() {
                if (xhr.status === 200) {
                    var data = JSON.parse(xhr.responseText);
                    if (data.media_type == "light_novel") {
                        data.media_type = "Light Novel";
                    } 
                    else if (data.media_type == "ova") {
                        data.media_type = "OVA";
                    } 
                    else if (data.media_type == "tv") {
                        data.media_type = "TV";
                    } 
                    else {
                        data.media_type = data.media_type.charAt(0).toUpperCase() + data.media_type.slice(1);
                    }
                    var output = data.title;
                    if (data.year) {
                        output += " (" + data.year + ")";
                    }
                    if (data.media_type) {
                        output += " [" + data.media_type + "]";
                    }

                    document.getElementById("modal-title-" + type + "_" + id).innerHTML = output;
                    document.getElementById("modal-body-" + type + "_" + id).innerHTML = data.html;
                    if (data.seasons_score) {
                        var select = document.getElementById("season-select-" + type + "_" + id);
                        var input = document.getElementById("score-input-" + type + "_" + id);
                        select.addEventListener("change", function() {
                            var selectedValue = select.value;
                            input.value = data.seasons_score[selectedValue];
                        });
                    }

                    if (!data.in_db) {
                        document.getElementById("modal-footer-" + type + "_" + id).querySelector('.modal-delete-btn').style.display = "none";
                      }

                }
            };
            
            xhr.send();

        };
    }
});