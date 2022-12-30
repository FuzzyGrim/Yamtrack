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

                    if (data.year) {
                        document.getElementById("modal-title-" + type + "_" + id).innerHTML = data.title + " (" + data.year + ")";
                    } 
                    else {
                        document.getElementById("modal-title-" + type + "_" + id).innerHTML = data.title;
                    }
                    
                    document.getElementById("modal-body-" + type + "_" + id).innerHTML = data.html;
                }
            };
            xhr.send();
            // var modal = document.getElementById("modal-" + type + "_" + id);
            // modal.style.display = "block";
        };
    }
});