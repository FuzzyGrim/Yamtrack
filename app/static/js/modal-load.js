$(document).ready(function() {
    var buttons = $(".open-modal-button");

    buttons.click(function() {
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
                if (data.seasons_details) {
                    var select = $("#season-select-" + type + "_" + id);

                    if (select.length) {
                        var input = $("#score-input-" + type + "_" + id);
                        var status = $("#season-status-" + type + "_" + id);
                        var progress = $("#progress-input-" + type + "_" + id);

                        select.change(function() {
                            var selectedValue = $(this).val();
                            if (selectedValue in data.seasons_details) {
                                input.val(data.seasons_details[selectedValue]["score"]);
                                status.val(data.seasons_details[selectedValue]["status"]);
                                progress.val(data.seasons_details[selectedValue]["progress"]);

                            }
                            else if (selectedValue == "all") {
                                input.val(data.score);
                                status.val(data.status);
                                progress.val(data.progress);
                            }
                            else {                            
                                input.val("");
                                status.val("Completed");
                                progress.val("");
                            }
                        });
                    }
                }
                if (data.in_db) {
                    // Add delete button if it doesn't exist
                    if ($("#modal-footer-" + type + "_" + id + " .modal-delete-btn").length == 0) {
                        console.log("yes")
                        var deleteButton = $('<button class="btn btn-danger modal-delete-btn" type="submit" name="delete">Delete</button>');
                        console.log(deleteButton);
                        $('#modal-footer-' + type + '_' + id + ' form').append(deleteButton);
                    }
                }
            }
        });
    });
});