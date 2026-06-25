import SwiftUI

struct DiaryEntryList<Destination: View>: View {
    let entries: [DiaryEntry]
    let artworkOverride: DiaryEntryArtworkOverride?
    @ViewBuilder let destination: (DiaryEntry) -> Destination

    init(
        entries: [DiaryEntry],
        artworkOverride: DiaryEntryArtworkOverride? = nil,
        @ViewBuilder destination: @escaping (DiaryEntry) -> Destination
    ) {
        self.entries = entries
        self.artworkOverride = artworkOverride
        self.destination = destination
    }

    var body: some View {
        ForEach(monthSections) { section in
            VStack(spacing: 0) {
                DiaryMonthHeader(title: section.title)

                ForEach(section.entries) { entry in
                    NavigationLink {
                        destination(entry)
                    } label: {
                        DiaryEntryRow(entry: entry, artworkOverride: artworkOverride)
                    }
                    .buttonStyle(.plain)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .padding(.bottom, 14)
        }
    }

    private var monthSections: [DiaryMonthSection] {
        entries.reduce(into: []) { sections, entry in
            let title = DiaryDateFormatter.monthHeader(from: entry.consumedAt ?? entry.createdAt) ?? "Undated"
            if sections.last?.title == title {
                sections[sections.count - 1].entries.append(entry)
            } else {
                sections.append(DiaryMonthSection(id: "\(sections.count)-\(title)", title: title, entries: [entry]))
            }
        }
    }
}

struct DiaryEntryArtworkOverride {
    let url: String?
    let orientation: PosterOrientation?
}

struct DiaryMonthSection: Identifiable {
    let id: String
    let title: String
    var entries: [DiaryEntry]
}

struct DiaryMonthHeader: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.system(size: 13, weight: .heavy))
            .foregroundStyle(.white.opacity(0.78))
            .textCase(.uppercase)
            .padding(.horizontal, 14)
            .frame(maxWidth: .infinity, minHeight: 36, alignment: .leading)
            .background(Color(red: 0.115, green: 0.108, blue: 0.095))
            .overlay(alignment: .leading) {
                Rectangle()
                    .fill(Color(red: 0.72, green: 0.74, blue: 0.76).opacity(0.68))
                    .frame(width: 3)
            }
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(.white.opacity(0.08))
                    .frame(height: 1)
            }
    }
}

struct DiaryEntryRow: View {
    let entry: DiaryEntry
    var artworkOverride: DiaryEntryArtworkOverride?

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            DiaryDateBadge(rawValue: entry.consumedAt ?? entry.createdAt, mediaType: entry.media.ref.mediaType)

            compactArtwork

            VStack(alignment: .leading, spacing: 5) {
                if hasRatingOrLike {
                    ratingLikeLine
                }

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
        .background(.white.opacity(0.065))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(.white.opacity(0.07))
                .frame(height: 1)
        }
    }

    private var compactArtwork: some View {
        MediaArtwork(
            url: artworkOverride?.url ?? entry.media.displayPosterURL,
            title: entry.media.title,
            slot: .diaryRow,
            mediaType: entry.media.ref.mediaType,
            orientation: artworkOverride?.orientation ?? entry.media.posterOrientation
        )
        .scaleEffect(0.75)
        .frame(width: 42, height: 63)
        .shadow(color: .black.opacity(0.24), radius: 9, y: 4)
    }

    private var hasRatingOrLike: Bool {
        clean(entry.rating) != nil || entry.liked
    }

    private var ratingLikeLine: some View {
        HStack(spacing: 6) {
            if let rating = clean(entry.rating) {
                DiaryStarRating(rating: rating)
            }

            if entry.liked {
                Image(systemName: "heart.fill")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.pink)
                    .accessibilityLabel("Liked")
            }
        }
    }

    private var titleLine: some View {
        return HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text(titleText)
                .font(.system(size: 15, weight: .heavy))
                .lineLimit(1)
                .minimumScaleFactor(0.65)

            if entry.isRewatch {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 11.25, weight: .bold))
                    .foregroundStyle(.white.opacity(0.7))
                    .accessibilityLabel("Rewatch")
            }
        }
    }

    private var titleText: AttributedString {
        let parts = titleParts
        var text = AttributedString(parts.title)
        text.foregroundColor = .white

        if let year = parts.year {
            var yearText = AttributedString(" (\(year))")
            yearText.foregroundColor = .white.opacity(0.48)
            text += yearText
        }

        return text
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

        chips += entry.tags.prefix(3).map { DiaryMetadataChip(text: $0, systemImage: nil) }

        if entry.tags.count > 3 {
            chips.append(DiaryMetadataChip(text: "+\(entry.tags.count - 3)", systemImage: nil))
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

struct DiaryStarRating: View {
    let rating: String

    var body: some View {
        HStack(spacing: 1) {
            ForEach(0..<5, id: \.self) { index in
                Image(systemName: symbolName(for: index))
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.yellow.opacity(0.92))
            }
        }
        .accessibilityLabel("Rating \(displayValue) out of 5 stars")
    }

    private var value: Double {
        guard let raw = Double(rating) else { return 0 }
        return max(0, min(5, raw / 2))
    }

    private var displayValue: String {
        value.formatted(.number.precision(.fractionLength(0...1)))
    }

    private func symbolName(for index: Int) -> String {
        let starValue = value - Double(index)
        if starValue >= 1 {
            return "star.fill"
        }
        if starValue >= 0.5 {
            return "star.leadinghalf.filled"
        }
        return "star"
    }
}

private struct DiaryMetadataChip: Hashable {
    let text: String
    let systemImage: String?
}

struct DiaryChip: View {
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
    let mediaType: String

    var body: some View {
        VStack(spacing: 3) {
            Text(day)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(.white.opacity(0.82))

            MediaTypeGlyph(theme: MediaTypeTheme.theme(for: mediaType), size: 11)
                .opacity(0.58)
        }
        .frame(width: 29, height: 47)
        .background(.white.opacity(0.09), in: RoundedRectangle(cornerRadius: 9, style: .continuous))
    }

    private var day: String {
        DiaryDateFormatter.dayNumber(from: rawValue) ?? "--"
    }
}

struct DiaryStateCard: View {
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

enum DiaryDateFormatter {
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
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    static func monthHeader(from rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return date.formatted(.dateTime.month(.wide).year())
    }

    static func exactDate(from rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return date.formatted(.dateTime.month(.abbreviated).day().year())
    }

    static func dayNumber(from rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return date.formatted(.dateTime.day())
    }

    private static func date(from rawValue: String?) -> Date? {
        guard let rawValue else { return nil }
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        return dateOnlyFormatter.date(from: String(trimmed.prefix(10)))
            ?? isoFormatter.date(from: trimmed)
            ?? fallbackISOFormatter.date(from: trimmed)
    }
}
