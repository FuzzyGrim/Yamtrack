// get hash from url
var hash = location.hash.replace(/^#/, '');
if (hash) {
    $('#v-pills-tab button[data-bs-target="#' + hash + '"]').click();

    // add active to media type if hash includes to status
    var media = hash.split('-')[0];
    $('#' + media + '-tab').addClass('active');
}
else {
    $('#v-pills-tab button[data-bs-target="#tv"]').click();
}

// change hash after click for page-reload
$('#v-pills-tab button').not('.dropdown-toggle').on('click', function () {
    var target = $(this).data('bs-target');
    window.location.hash = target;

    // add active to media type if hash includes to status
    var media = target.split('-')[0];
    $(media + '-tab').addClass('active');
});

// remove hash when search
$('#navbar-form').on('submit', function() {
    location.hash = '';
});