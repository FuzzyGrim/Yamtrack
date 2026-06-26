import Foundation
import SwiftUI

@MainActor
@Observable
final class PersonDetailViewModel {
    var detail: PersonDetail?
    var filmography: [MediaSummary] = []
    var isLoading = false
    var errorMessage: String?

    private let ref: PersonRef
    private let peopleRepository: PeopleRepository
    private let onUnauthorized: () -> Void

    init(
        ref: PersonRef,
        peopleRepository: PeopleRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.ref = ref
        self.peopleRepository = peopleRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let loaded = try await peopleRepository.detail(ref: ref)
            detail = loaded
            filmography = Self.uniqueFilmography(from: loaded.filmography)
        } catch {
            detail = nil
            filmography = []
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    static func uniqueFilmography(from media: [MediaSummary]) -> [MediaSummary] {
        var seen = Set<String>()
        return media.filter { seen.insert($0.id).inserted }
    }
}

struct PersonDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: PersonDetailViewModel
    @State private var selectedRef: MediaRef?
    @State private var edgeDragOffset: CGFloat = 0

    private let peopleRepository: PeopleRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        ref: PersonRef,
        peopleRepository: PeopleRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository = AppRepositories.current().lists,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.peopleRepository = peopleRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: PersonDetailViewModel(
            ref: ref,
            peopleRepository: peopleRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            SpinePageBackground()

            content

            PersonBackButton {
                dismiss()
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
        }
        .toolbar(.hidden, for: .tabBar)
        .navigationBarBackButtonHidden()
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 28)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .fullScreenCover(item: $selectedRef, onDismiss: { selectedRef = nil }) { ref in
            MediaDetailView(
                ref: ref,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .task {
            if viewModel.detail == nil {
                await viewModel.load()
            }
        }
    }

    private var edgeSwipeBackGesture: some Gesture {
        DragGesture(minimumDistance: 12, coordinateSpace: .global)
            .onChanged { value in
                guard value.translation.width > 0 else { return }
                edgeDragOffset = value.translation.width
            }
            .onEnded { value in
                if value.translation.width > 90 {
                    dismiss()
                } else {
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                        edgeDragOffset = 0
                    }
                }
            }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let detail = viewModel.detail {
            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 26) {
                    hero(detail)
                    biographySection(detail)
                    filmographySection
                }
                .padding(.horizontal, 16)
                .padding(.top, 44)
                .padding(.bottom, 36)
            }
            .scrollContentBackground(.hidden)
            .refreshable {
                await viewModel.load()
            }
        } else if let error = viewModel.errorMessage {
            ContentUnavailableView(
                "Could not load person",
                systemImage: "exclamationmark.triangle",
                description: Text(error)
            )
            .foregroundStyle(.white)
            .padding()
        }
    }

    private func hero(_ detail: PersonDetail) -> some View {
        VStack(spacing: 16) {
            PersonProfileImage(urlString: detail.profileUrl, name: detail.name)
                .frame(width: 156, height: 156)
                .shadow(color: .black.opacity(0.42), radius: 24, y: 14)

            VStack(spacing: 10) {
                Text(detail.name)
                    .font(.system(size: 34, weight: .black))
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.center)
                    .lineLimit(3)
                    .minimumScaleFactor(0.72)
                    .frame(maxWidth: .infinity)

                personChips(detail)
            }
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private func personChips(_ detail: PersonDetail) -> some View {
        let chips = metadataChips(detail)
        if !chips.isEmpty {
            GeometryReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(chips, id: \.self) { chip in
                            Text(chip)
                                .font(.system(size: 11, weight: .bold))
                                .foregroundStyle(.white.opacity(0.82))
                                .lineLimit(1)
                                .padding(.horizontal, 11)
                                .frame(height: 31)
                                .background(.white.opacity(0.12), in: Capsule())
                        }
                    }
                    .frame(minWidth: proxy.size.width)
                }
                .mask(alignment: .trailing) {
                    LinearGradient(
                        stops: [
                            .init(color: .black, location: 0),
                            .init(color: .black, location: 0.9),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                }
            }
            .frame(height: 31)
        }
    }

    @ViewBuilder
    private func biographySection(_ detail: PersonDetail) -> some View {
        if let biography = clean(detail.biography) {
            VStack(alignment: .leading, spacing: 12) {
                PersonSectionLabel(title: "Biography")
                PersonBiographyCard(text: biography)
            }
        }
    }

    private var filmographySection: some View {
        VStack(alignment: .leading, spacing: 14) {
            PersonSectionLabel(title: "Filmography")

            if viewModel.filmography.isEmpty {
                ContentUnavailableView(
                    "No filmography",
                    systemImage: "square.grid.2x2",
                    description: Text("TMDB credits will appear here when available.")
                )
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity, minHeight: 220)
            } else {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 10) {
                    ForEach(viewModel.filmography) { media in
                        Button {
                            selectedRef = media.ref
                        } label: {
                            MediaArtwork(
                                url: media.displayPosterURL,
                                title: media.title,
                                slot: .tagGrid,
                                mediaType: media.ref.mediaType,
                                orientation: media.posterOrientation
                            )
                            .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(media.title)")
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func metadataChips(_ detail: PersonDetail) -> [String] {
        var chips: [String] = []
        if let department = clean(detail.knownForDepartment) {
            chips.append(department)
        }
        if let birthDate = clean(detail.birthDate) {
            chips.append("Born \(yearOrDate(birthDate))")
        }
        if let deathDate = clean(detail.deathDate) {
            chips.append("Died \(yearOrDate(deathDate))")
        }
        if let place = clean(detail.placeOfBirth) {
            chips.append(place)
        }
        return chips
    }

    private func clean(_ value: String?) -> String? {
        guard let text = value?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else {
            return nil
        }
        return text
    }

    private func yearOrDate(_ value: String) -> String {
        value.count >= 4 ? String(value.prefix(4)) : value
    }
}

private struct PersonProfileImage: View {
    let urlString: String?
    let name: String

    var body: some View {
        AsyncImage(url: imageURL) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                Circle()
                    .fill(.white.opacity(0.12))
                    .overlay {
                        Image(systemName: "person.fill")
                            .font(.system(size: 58, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.72))
                    }
            }
        }
        .clipShape(Circle())
        .overlay {
            Circle().stroke(.white.opacity(0.16), lineWidth: 1)
        }
        .accessibilityLabel(name)
    }

    private var imageURL: URL? {
        guard let urlString, !urlString.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return URL(string: urlString)
    }
}

private struct PersonBiographyCard: View {
    let text: String
    @State private var isExpanded = false
    @State private var truncatedHeight: CGFloat = 0
    @State private var fullHeight: CGFloat = 0

    private var canExpand: Bool {
        fullHeight > truncatedHeight + 1
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(text)
                .font(biographyFont)
                .foregroundStyle(.white.opacity(0.9))
                .lineSpacing(2)
                .lineLimit(isExpanded ? nil : 3)
                .background {
                    measuredText(lineLimit: 3, key: PersonBiographyTruncatedHeightKey.self)
                }
                .background {
                    measuredText(lineLimit: nil, key: PersonBiographyFullHeightKey.self)
                }

            if canExpand {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        isExpanded.toggle()
                    }
                } label: {
                    Label(isExpanded ? "READ LESS" : "READ MORE", systemImage: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.62))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 8))
        .onPreferenceChange(PersonBiographyTruncatedHeightKey.self) { truncatedHeight = $0 }
        .onPreferenceChange(PersonBiographyFullHeightKey.self) { fullHeight = $0 }
    }

    private func measuredText<Key: PreferenceKey>(lineLimit: Int?, key: Key.Type) -> some View where Key.Value == CGFloat {
        Text(text)
            .font(biographyFont)
            .lineSpacing(2)
            .lineLimit(lineLimit)
            .fixedSize(horizontal: false, vertical: true)
            .background {
                GeometryReader { proxy in
                    Color.clear.preference(key: key, value: proxy.size.height)
                }
            }
            .hidden()
    }

    private var biographyFont: Font {
        .system(size: 14, weight: .semibold)
    }
}

private struct PersonBiographyTruncatedHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct PersonBiographyFullHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct PersonBackButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "chevron.left")
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(.black.opacity(0.34), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Back")
    }
}

private struct PersonSectionLabel: View {
    let title: String

    var body: some View {
        Text(title.uppercased())
            .font(.system(size: 12, weight: .heavy))
            .foregroundStyle(.white.opacity(0.54))
            .tracking(1.2)
    }
}
