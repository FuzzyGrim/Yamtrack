import SwiftUI

struct PosterImage: View {
    let urlString: String?
    let title: String
    var slot: PosterSlot = .carousel
    var mediaType: String?
    var orientation: PosterOrientation?
    var contentMode: PosterContentMode = .fill

    var body: some View {
        MediaArtwork(
            url: urlString,
            title: title,
            slot: slot,
            mediaType: mediaType,
            orientation: orientation,
            contentMode: contentMode
        )
    }
}
