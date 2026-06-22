import SwiftUI

struct MediaLensAtmosphere: ViewModifier {
    let theme: MediaTypeTheme

    func body(content: Content) -> some View {
        content
    }
}

extension View {
    func mediaLensAtmosphere(theme: MediaTypeTheme) -> some View {
        modifier(MediaLensAtmosphere(theme: theme))
    }
}

#Preview {
    NavigationStack {
        VStack {
            Spacer()
            MediaLensChip(selectedType: "game") {}
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.black)
        .navigationTitle("Search")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.hidden, for: .navigationBar)
        .mediaLensAtmosphere(theme: .theme(for: "game"))
    }
}
