import SwiftUI

@MainActor
@Observable
final class DiaryViewModel {
    var entries: [DiaryEntry] = []
    var isLoading = false
    var errorMessage: String?

    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void

    init(diaryRepository: DiaryRepository, onUnauthorized: @escaping () -> Void) {
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            entries = try await diaryRepository.list()
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct DiaryView: View {
    @State private var viewModel: DiaryViewModel

    init(diaryRepository: DiaryRepository, mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void = {}) {
        _viewModel = State(initialValue: DiaryViewModel(diaryRepository: diaryRepository, onUnauthorized: onUnauthorized))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                SpinePageBackground()

                ScrollView(showsIndicators: false) {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        header

                        if viewModel.isLoading {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 320)
                        } else if let error = viewModel.errorMessage {
                            DiaryStateCard(
                                title: "Could not load diary",
                                systemImage: "exclamationmark.triangle",
                                message: error
                            )
                        } else if viewModel.entries.isEmpty {
                            DiaryStateCard(
                                title: "No diary entries",
                                systemImage: "calendar",
                                message: "Logs you create from media pages will appear here."
                            )
                        } else {
                            ForEach(viewModel.entries) { entry in
                                DiaryEntryRow(entry: entry)
                            }
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.top, 18)
                    .padding(.bottom, 28)
                }
                .refreshable {
                    await viewModel.load()
                }
            }
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbarBackground(.hidden, for: .navigationBar)
            .task {
                await viewModel.load()
            }
            .onReceive(NotificationCenter.default.publisher(for: .letterboxdImportDidSucceed)) { _ in
                Task { await viewModel.load() }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Diary")
                .font(.system(size: 32, weight: .black))
                .foregroundStyle(.white)

            Text("Your logged watches, reads, plays, and reviews.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.58))
        }
        .padding(.bottom, 6)
    }
}

private struct DiaryEntryRow: View {
    let entry: DiaryEntry

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            DiaryDateBadge(rawValue: entry.consumedAt ?? entry.createdAt)

            compactArtwork

            VStack(alignment: .leading, spacing: 5) {
                titleLine

                if let title = clean(entry.reviewTitle) {
                    Text(title)
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.white.opacity(0.82))
                        .lineLimit(2)
                }

                if let review = clean(entry.review) {
                    Text(review)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(.white.opacity(0.58))
                        .lineLimit(2)
                }

                if !metadataChips.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(metadataChips, id: \.self) { chip in
                                DiaryChip(text: chip.text, systemImage: chip.systemImage)
                            }
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(10)
        .background(.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(.white.opacity(0.08))
        }
    }

    private var compactArtwork: some View {
        MediaArtwork(
            url: entry.media.displayPosterURL,
            title: entry.media.title,
            slot: .diaryRow,
            mediaType: entry.media.ref.mediaType,
            orientation: entry.media.posterOrientation
        )
        .scaleEffect(0.75)
        .frame(width: 42, height: 63)
        .shadow(color: .black.opacity(0.24), radius: 9, y: 4)
    }

    private var titleLine: some View {
        let parts = titleParts
        return HStack(alignment: .firstTextBaseline, spacing: 0) {
            Text(parts.title)
                .font(.system(size: 15, weight: .heavy))
                .foregroundStyle(.white)
                .lineLimit(2)

            if let year = parts.year {
                Text(" (\(year))")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.48))
                    .lineLimit(1)
            }
        }
    }

    private var titleParts: (title: String, year: String?) {
        let title = entry.media.title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard title.hasSuffix(")") else {
            return (title, nil)
        }

        let suffix = String(title.suffix(6))
        guard suffix.first == "(", suffix.last == ")" else {
            return (title, nil)
        }

        let year = String(suffix.dropFirst().dropLast())
        guard year.count == 4, year.allSatisfy(\.isNumber) else {
            return (title, nil)
        }

        return (String(title.dropLast(7)), year)
    }

    private var metadataChips: [DiaryMetadataChip] {
        var chips: [DiaryMetadataChip] = []

        if let rating = clean(entry.rating) {
            chips.append(DiaryMetadataChip(text: rating, systemImage: "star.fill"))
        }

        chips += entry.tags.prefix(3).map { DiaryMetadataChip(text: $0, systemImage: nil) }

        if entry.tags.count > 3 {
            chips.append(DiaryMetadataChip(text: "+\(entry.tags.count - 3)", systemImage: nil))
        }

        if entry.isRewatch {
            chips.append(DiaryMetadataChip(text: "Rewatch", systemImage: "arrow.clockwise"))
        }

        if entry.containsSpoilers {
            chips.append(DiaryMetadataChip(text: "Spoilers", systemImage: "eye.slash"))
        }

        return chips
    }

    private func clean(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

private struct DiaryMetadataChip: Hashable {
    let text: String
    let systemImage: String?
}

private struct DiaryChip: View {
    let text: String
    var systemImage: String?

    var body: some View {
        Label {
            Text(text)
                .lineLimit(1)
        } icon: {
            if let systemImage {
                Image(systemName: systemImage)
                    .font(.system(size: 9, weight: .black))
            }
        }
        .font(.system(size: 10, weight: .bold))
        .foregroundStyle(.white.opacity(0.78))
        .padding(.horizontal, 7)
        .frame(height: 21)
        .background(.white.opacity(0.11), in: Capsule())
    }
}

private struct DiaryDateBadge: View {
    let rawValue: String?

    var body: some View {
        VStack(spacing: 2) {
            Text(month)
                .font(.system(size: 10, weight: .black))
                .foregroundStyle(.white.opacity(0.58))
            Text(day)
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(.white)
            Text(year)
                .font(.system(size: 9, weight: .heavy))
                .foregroundStyle(.white.opacity(0.44))
        }
        .frame(width: 38, height: 63)
        .background(.white.opacity(0.09), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private var month: String {
        DiaryDateFormatter.month(from: rawValue) ?? "---"
    }

    private var day: String {
        DiaryDateFormatter.day(from: rawValue) ?? "--"
    }

    private var year: String {
        DiaryDateFormatter.year(from: rawValue) ?? ""
    }
}

private struct DiaryStateCard: View {
    let title: String
    let systemImage: String
    let message: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.system(size: 32, weight: .bold))
                .foregroundStyle(.white.opacity(0.78))
            Text(title)
                .font(.system(size: 18, weight: .heavy))
                .foregroundStyle(.white)
            Text(message)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white.opacity(0.58))
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, minHeight: 240)
        .padding(22)
        .background(.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(.white.opacity(0.08))
        }
    }
}

private enum DiaryDateFormatter {
    private static let isoFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let fallbackISOFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let dateOnlyFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    static func month(from rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return date.formatted(.dateTime.month(.abbreviated)).uppercased()
    }

    static func day(from rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return date.formatted(.dateTime.day())
    }

    static func year(from rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return date.formatted(.dateTime.year())
    }

    private static func date(from rawValue: String?) -> Date? {
        guard let rawValue else { return nil }
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        return isoFormatter.date(from: trimmed)
            ?? fallbackISOFormatter.date(from: trimmed)
            ?? dateOnlyFormatter.date(from: trimmed)
    }
}
