// get hash from url
var hash = location.hash.replace(/^#/, '');
if (hash) {
    $('#v-pills-tab button[data-bs-target="#' + hash + '"]').click();

    // highlight media type if hash includes to status
    var media = hash.split('-')[0];
    $('#' + media + '-tab').addClass('highlight');
}
else {
    $('#v-pills-tab button[data-bs-target="#tv"]').click();
}


$('#v-pills-tab button').not('.dropdown-toggle').on('click', function () {
    // change hash after click for page-reload
    var target = $(this).data('bs-target');
    window.location.hash = target;

    // Remove the "highlight" class from all tab link elements
    $('#v-pills-tab button').removeClass('highlight');

    // highlight media type if hash includes to status
    var media = target.split('-')[0];
    $(media + '-tab').addClass('highlight');
});