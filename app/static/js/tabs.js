// get hash from url
var hash = location.hash.replace(/^#/, '');
if (hash) {
    $('#v-pills-tab button[data-bs-target="#' + hash + '"]').click();
}
else {
    $('#v-pills-tab button[data-bs-target="#tv"]').click();
}

// change hash for page-reload
$('#v-pills-tab button').not('.dropdown-toggle').on('click', function (e) {
window.location.hash = $(this).data('bs-target');
});