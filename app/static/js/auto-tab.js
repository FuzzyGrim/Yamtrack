// get hash from url
$(function() {

    var hash = location.hash.replace(/^#\//, '');
    if (hash) {
        $('#v-pills-tab button[data-bs-target="#'+hash+'"]').tab('show');

        // highlight media type if hash includes to status
        var media = hash.split('-')[0];
        $('#' + media + '-tab').addClass('highlight');
    }
    else {
        $('#v-pills-tab button[data-bs-target="#tv"]').tab('show');
    }


    $('#v-pills-tab button').not('.dropdown-toggle').on('click', function (event) {
        event.preventDefault();
        // change hash after click for page-reload
        var target = $(this).data('bs-target').replace(/^#/, '');
        window.location.hash = '#/' + target;

        // Remove the "highlight" class from all tab link elements
        $('#v-pills-tab button').removeClass('highlight');

        // highlight media type if hash includes to status
        var media = target.split('-')[0];
        $(media + '-tab').addClass('highlight');
    });

});