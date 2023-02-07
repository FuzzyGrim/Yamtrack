$(document).on("click", ".open-modal-button", function() {

    var url = $(this).data("url");
    var parts = url.split('/');
    var type = parts[2];
    var id = parts[3];

    $.ajax({
        type: 'GET',
        url: url,
        success: function(data) {
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

            $("#modal-title-" + type + "_" + id).html(output);
            $("#modal-body-" + type + "_" + id).html(data.html);
            if (data.media_seasons) {
                var select = $("#season-select-" + type + "_" + id);

                if (select.length) {
                    var score = $("#score-input-" + type + "_" + id);
                    var status = $("#season-status-" + type + "_" + id);
                    var progress = $("#progress-input-" + type + "_" + id);
                    var start = $("#start-input-" + type + "_" + id);
                    var end = $("#end-input-" + type + "_" + id);

                    select.change(function() {
                        var selectedValue = $(this).val();

                        if (selectedValue == "general") {
                            score.val(data.score);
                            status.val(data.status);
                            progress.val(data.progress);
                            start.val(data.start_date);
                            end.val(data.end_date);
                        }
                        else {
                            let exists = false;

                            // check if season exists in database
                            for(let i = 0; i < data["media_seasons"].length && !exists; i++) {
                                let media_season = data["media_seasons"][i];
                                if (media_season.number == selectedValue){
                                    score.val(media_season.score);
                                    status.val(media_season.status);
                                    progress.val(media_season.progress);
                                    start.val(media_season.start_date);
                                    end.val(media_season.end_date);
                                    exists = true;
                                }
                            }

                            if (!exists) {
                                score.val("");
                                status.val("Completed");
                                progress.val("");
                                start.val("");
                                end.val("");
                            }
                        }
                    });
                }
            }
            if (data.in_db) {
                // Add delete button if it doesn't exist
                if ($("#modal-footer-" + type + "_" + id + " .modal-delete-btn").length == 0) {
                    var deleteButton = $('<button class="btn btn-danger modal-delete-btn" type="submit" name="delete">Delete</button>');
                    $('#modal-footer-' + type + '_' + id + ' form').append(deleteButton);
                }
            }

        }
    });
});