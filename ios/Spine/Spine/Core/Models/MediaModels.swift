import Foundation

struct MediaRef: Codable, Hashable, Identifiable {
    let itemId: Int?
    let source: String
    let mediaType: String
    let mediaId: String
    let seasonNumber: Int?
    let episodeNumber: Int?

    var id: String {
        [
            source,
            mediaType,
            mediaId,
            seasonNumber.map(String.init) ?? "_",
            episodeNumber.map(String.init) ?? "_",
        ].joined(separator: ":")
    }
}

struct MediaSummary: Codable, Identifiable, Hashable {
    let ref: MediaRef
    let title: String
    let subtitle: String?
    let overview: String?
    let imageUrl: String?
    let posterUrl: String?
    let customPosterUrl: String?
    let backdropUrl: String?
    let customBackdropUrl: String?
    let posterOrientation: PosterOrientation?
    let posterAspectRatio: Double?
    let posterWidth: Int?
    let posterHeight: Int?
    let posterAccentColor: String?
    let logoUrl: String?
    let logoWidth: Int?
    let logoHeight: Int?
    let logoAspectRatio: Double?
    let releaseDate: String?
    let defaultSource: String?
    let position: Int?
    var userState: UserMediaState?

    var id: String { ref.id }

    var displayPosterURL: String? {
        customPosterUrl ?? posterUrl ?? imageUrl
    }

    var displayBackdropURL: String? {
        customBackdropUrl ?? backdropUrl
    }

    enum CodingKeys: String, CodingKey {
        case ref
        case title
        case subtitle
        case overview
        case imageUrl
        case posterUrl
        case customPosterUrl
        case backdropUrl
        case customBackdropUrl
        case posterOrientation
        case posterAspectRatio
        case posterWidth
        case posterHeight
        case posterAccentColor
        case logoUrl
        case logoWidth
        case logoHeight
        case logoAspectRatio
        case releaseDate
        case defaultSource
        case position
        case userState
    }

    init(
        ref: MediaRef,
        title: String,
        subtitle: String? = nil,
        overview: String? = nil,
        imageUrl: String? = nil,
        posterUrl: String? = nil,
        customPosterUrl: String? = nil,
        backdropUrl: String? = nil,
        customBackdropUrl: String? = nil,
        posterOrientation: PosterOrientation? = nil,
        posterAspectRatio: Double? = nil,
        posterWidth: Int? = nil,
        posterHeight: Int? = nil,
        posterAccentColor: String? = nil,
        logoUrl: String? = nil,
        logoWidth: Int? = nil,
        logoHeight: Int? = nil,
        logoAspectRatio: Double? = nil,
        releaseDate: String? = nil,
        defaultSource: String? = nil,
        position: Int? = nil,
        userState: UserMediaState? = nil
    ) {
        self.ref = ref
        self.title = title
        self.subtitle = subtitle
        self.overview = overview
        self.imageUrl = imageUrl
        self.posterUrl = posterUrl ?? imageUrl
        self.customPosterUrl = customPosterUrl
        self.backdropUrl = backdropUrl
        self.customBackdropUrl = customBackdropUrl
        self.posterOrientation = posterOrientation
        self.posterAspectRatio = posterAspectRatio
        self.posterWidth = posterWidth
        self.posterHeight = posterHeight
        self.posterAccentColor = posterAccentColor
        self.logoUrl = logoUrl
        self.logoWidth = logoWidth
        self.logoHeight = logoHeight
        self.logoAspectRatio = logoAspectRatio
        self.releaseDate = releaseDate
        self.defaultSource = defaultSource
        self.position = position
        self.userState = userState
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        self.init(
            ref: try container.decode(MediaRef.self, forKey: .ref),
            title: try container.decode(String.self, forKey: .title),
            subtitle: try container.decodeIfPresent(String.self, forKey: .subtitle),
            overview: try container.decodeIfPresent(String.self, forKey: .overview),
            imageUrl: imageUrl,
            posterUrl: try container.decodeIfPresent(String.self, forKey: .posterUrl) ?? imageUrl,
            customPosterUrl: try container.decodeIfPresent(String.self, forKey: .customPosterUrl),
            backdropUrl: try container.decodeIfPresent(String.self, forKey: .backdropUrl),
            customBackdropUrl: try container.decodeIfPresent(String.self, forKey: .customBackdropUrl),
            posterOrientation: try container.decodeIfPresent(PosterOrientation.self, forKey: .posterOrientation),
            posterAspectRatio: try container.decodeIfPresent(Double.self, forKey: .posterAspectRatio),
            posterWidth: try container.decodeIfPresent(Int.self, forKey: .posterWidth),
            posterHeight: try container.decodeIfPresent(Int.self, forKey: .posterHeight),
            posterAccentColor: try container.decodeIfPresent(String.self, forKey: .posterAccentColor),
            logoUrl: try container.decodeIfPresent(String.self, forKey: .logoUrl),
            logoWidth: try container.decodeIfPresent(Int.self, forKey: .logoWidth),
            logoHeight: try container.decodeIfPresent(Int.self, forKey: .logoHeight),
            logoAspectRatio: try container.decodeIfPresent(Double.self, forKey: .logoAspectRatio),
            releaseDate: try container.decodeIfPresent(String.self, forKey: .releaseDate),
            defaultSource: try container.decodeIfPresent(String.self, forKey: .defaultSource),
            position: try container.decodeIfPresent(Int.self, forKey: .position),
            userState: try container.decodeIfPresent(UserMediaState.self, forKey: .userState)
        )
    }
}

struct MediaDiscoverRequest: Hashable, Identifiable {
    enum Filter: Hashable {
        case genre(String)
        case year(String)
        case platform(String)

        var value: String {
            switch self {
            case let .genre(value), let .year(value), let .platform(value):
                value
            }
        }

        var queryItem: URLQueryItem {
            switch self {
            case let .genre(value):
                URLQueryItem(name: "genre", value: value)
            case let .year(value):
                URLQueryItem(name: "year", value: value)
            case let .platform(value):
                URLQueryItem(name: "platform", value: value)
            }
        }
    }

    let mediaType: String
    let source: String?
    let filter: Filter
    var page: String?
    var pageSize: Int?

    var id: String {
        [mediaType, source ?? "_", filter.queryItem.name, filter.value].joined(separator: ":")
    }

    var queryItems: [URLQueryItem] {
        var items = [
            URLQueryItem(name: "media_type", value: mediaType),
            URLQueryItem(name: "sort", value: "vote_count"),
            filter.queryItem,
        ]
        if let source {
            items.append(URLQueryItem(name: "source", value: source))
        }
        if let page {
            items.append(URLQueryItem(name: "page", value: page))
        }
        if let pageSize {
            items.append(URLQueryItem(name: "page_size", value: String(pageSize)))
        }
        return items
    }

    var title: String {
        "\(filter.value) · \(MediaTypeTheme.theme(for: mediaType).displayName)"
    }

    static func detailPillRequest(ref: MediaRef, filter: Filter) -> MediaDiscoverRequest? {
        let mediaType = ref.mediaType == "season" ? "tv" : ref.mediaType
        switch (mediaType, filter) {
        case ("movie", .genre), ("movie", .year),
             ("tv", .genre), ("tv", .year),
             ("book", .genre), ("book", .year),
             ("game", .genre), ("game", .year), ("game", .platform):
            return MediaDiscoverRequest(
                mediaType: mediaType,
                source: ref.source,
                filter: filter,
                page: nil,
                pageSize: nil
            )
        default:
            return nil
        }
    }
}

struct MediaDetail: Decodable, Identifiable {
    let ref: MediaRef
    let title: String
    let subtitle: String?
    let overview: String?
    let synopsis: String?
    let imageUrl: String?
    let posterUrl: String?
    let posterOrientation: PosterOrientation?
    let posterAspectRatio: Double?
    let posterWidth: Int?
    let posterHeight: Int?
    let posterAccentColor: String?
    let logoUrl: String?
    let logoWidth: Int?
    let logoHeight: Int?
    let logoAspectRatio: Double?
    let releaseDate: String?
    let defaultSource: String?
    let userState: UserMediaState?
    let backdropUrl: String?
    let details: [String: JSONValue]?
    let related: [String: JSONValue]?
    let providers: JSONValue?
    let community: CommunityStats?
    let externalRatings: [ExternalRating]?
    let reviews: [MediaReview]?
    let cast: [CreditPerson]?
    let crew: [CreditPerson]?
    let relatedSections: [RelatedMediaSection]?
    let episodes: [EpisodeSummary]?
    let seasons: [SeasonSummary]?
    let customPosterUrl: String?
    let customBackdropUrl: String?

    var id: String { ref.id }

    var displayPosterURL: String? {
        customPosterUrl ?? posterUrl ?? imageUrl
    }

    var displayBackdropURL: String? {
        customBackdropUrl ?? backdropUrl
    }

    enum CodingKeys: String, CodingKey {
        case ref
        case title
        case subtitle
        case overview
        case synopsis
        case imageUrl
        case posterUrl
        case posterOrientation
        case posterAspectRatio
        case posterWidth
        case posterHeight
        case posterAccentColor
        case logoUrl
        case logoWidth
        case logoHeight
        case logoAspectRatio
        case releaseDate
        case defaultSource
        case userState
        case backdropUrl
        case details
        case related
        case providers
        case community
        case externalRatings
        case reviews
        case cast
        case crew
        case relatedSections
        case episodes
        case seasons
        case customPosterUrl
        case customBackdropUrl
    }

    init(
        ref: MediaRef,
        title: String,
        subtitle: String? = nil,
        overview: String? = nil,
        synopsis: String? = nil,
        imageUrl: String? = nil,
        posterUrl: String? = nil,
        posterOrientation: PosterOrientation? = nil,
        posterAspectRatio: Double? = nil,
        posterWidth: Int? = nil,
        posterHeight: Int? = nil,
        posterAccentColor: String? = nil,
        logoUrl: String? = nil,
        logoWidth: Int? = nil,
        logoHeight: Int? = nil,
        logoAspectRatio: Double? = nil,
        releaseDate: String? = nil,
        defaultSource: String? = nil,
        userState: UserMediaState? = nil,
        backdropUrl: String? = nil,
        details: [String: JSONValue]? = nil,
        related: [String: JSONValue]? = nil,
        providers: JSONValue? = nil,
        community: CommunityStats? = nil,
        externalRatings: [ExternalRating]? = nil,
        reviews: [MediaReview]? = nil,
        cast: [CreditPerson]? = nil,
        crew: [CreditPerson]? = nil,
        relatedSections: [RelatedMediaSection]? = nil,
        episodes: [EpisodeSummary]? = nil,
        seasons: [SeasonSummary]? = nil,
        customPosterUrl: String? = nil,
        customBackdropUrl: String? = nil
    ) {
        self.ref = ref
        self.title = title
        self.subtitle = subtitle
        self.overview = overview
        self.synopsis = synopsis
        self.imageUrl = imageUrl
        self.posterUrl = posterUrl ?? imageUrl
        self.posterOrientation = posterOrientation
        self.posterAspectRatio = posterAspectRatio
        self.posterWidth = posterWidth
        self.posterHeight = posterHeight
        self.posterAccentColor = posterAccentColor
        self.logoUrl = logoUrl
        self.logoWidth = logoWidth
        self.logoHeight = logoHeight
        self.logoAspectRatio = logoAspectRatio
        self.releaseDate = releaseDate
        self.defaultSource = defaultSource
        self.userState = userState
        self.backdropUrl = backdropUrl
        self.details = details
        self.related = related
        self.providers = providers
        self.community = community
        self.externalRatings = externalRatings
        self.reviews = reviews
        self.cast = cast
        self.crew = crew
        self.relatedSections = relatedSections
        self.episodes = episodes
        self.seasons = seasons
        self.customPosterUrl = customPosterUrl
        self.customBackdropUrl = customBackdropUrl
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        self.init(
            ref: try container.decode(MediaRef.self, forKey: .ref),
            title: try container.decode(String.self, forKey: .title),
            subtitle: try container.decodeIfPresent(String.self, forKey: .subtitle),
            overview: try container.decodeIfPresent(String.self, forKey: .overview),
            synopsis: try container.decodeIfPresent(String.self, forKey: .synopsis),
            imageUrl: imageUrl,
            posterUrl: try container.decodeIfPresent(String.self, forKey: .posterUrl) ?? imageUrl,
            posterOrientation: try container.decodeIfPresent(PosterOrientation.self, forKey: .posterOrientation),
            posterAspectRatio: try container.decodeIfPresent(Double.self, forKey: .posterAspectRatio),
            posterWidth: try container.decodeIfPresent(Int.self, forKey: .posterWidth),
            posterHeight: try container.decodeIfPresent(Int.self, forKey: .posterHeight),
            posterAccentColor: try container.decodeIfPresent(String.self, forKey: .posterAccentColor),
            logoUrl: try container.decodeIfPresent(String.self, forKey: .logoUrl),
            logoWidth: try container.decodeIfPresent(Int.self, forKey: .logoWidth),
            logoHeight: try container.decodeIfPresent(Int.self, forKey: .logoHeight),
            logoAspectRatio: try container.decodeIfPresent(Double.self, forKey: .logoAspectRatio),
            releaseDate: try container.decodeIfPresent(String.self, forKey: .releaseDate),
            defaultSource: try container.decodeIfPresent(String.self, forKey: .defaultSource),
            userState: try container.decodeIfPresent(UserMediaState.self, forKey: .userState),
            backdropUrl: try container.decodeIfPresent(String.self, forKey: .backdropUrl),
            details: try container.decodeIfPresent([String: JSONValue].self, forKey: .details),
            related: try container.decodeIfPresent([String: JSONValue].self, forKey: .related),
            providers: try container.decodeIfPresent(JSONValue.self, forKey: .providers),
            community: try container.decodeIfPresent(CommunityStats.self, forKey: .community),
            externalRatings: try container.decodeIfPresent([ExternalRating].self, forKey: .externalRatings),
            reviews: try container.decodeIfPresent([MediaReview].self, forKey: .reviews),
            cast: try container.decodeIfPresent([CreditPerson].self, forKey: .cast),
            crew: try container.decodeIfPresent([CreditPerson].self, forKey: .crew),
            relatedSections: try container.decodeIfPresent([RelatedMediaSection].self, forKey: .relatedSections),
            episodes: try container.decodeIfPresent([EpisodeSummary].self, forKey: .episodes),
            seasons: try container.decodeIfPresent([SeasonSummary].self, forKey: .seasons),
            customPosterUrl: try container.decodeIfPresent(String.self, forKey: .customPosterUrl),
            customBackdropUrl: try container.decodeIfPresent(String.self, forKey: .customBackdropUrl)
        )
    }

    func replacingPoster(with response: PosterSaveResponse) -> MediaDetail {
        MediaDetail(
            ref: ref,
            title: title,
            subtitle: subtitle,
            overview: overview,
            synopsis: synopsis,
            imageUrl: imageUrl,
            posterUrl: response.customPosterUrl ?? response.posterUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: response.posterAccentColor ?? posterAccentColor,
            logoUrl: logoUrl,
            logoWidth: logoWidth,
            logoHeight: logoHeight,
            logoAspectRatio: logoAspectRatio,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: userState,
            backdropUrl: backdropUrl,
            details: details,
            related: related,
            providers: providers,
            community: community,
            externalRatings: externalRatings,
            reviews: reviews,
            cast: cast,
            crew: crew,
            relatedSections: relatedSections,
            episodes: episodes,
            seasons: seasons,
            customPosterUrl: response.customPosterUrl ?? response.posterUrl,
            customBackdropUrl: customBackdropUrl
        )
    }

    func replacingBackdrop(with response: BackdropSaveResponse) -> MediaDetail {
        MediaDetail(
            ref: ref,
            title: title,
            subtitle: subtitle,
            overview: overview,
            synopsis: synopsis,
            imageUrl: imageUrl,
            posterUrl: posterUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: posterAccentColor,
            logoUrl: logoUrl,
            logoWidth: logoWidth,
            logoHeight: logoHeight,
            logoAspectRatio: logoAspectRatio,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: userState,
            backdropUrl: backdropUrl,
            details: details,
            related: related,
            providers: providers,
            community: community,
            externalRatings: externalRatings,
            reviews: reviews,
            cast: cast,
            crew: crew,
            relatedSections: relatedSections,
            episodes: episodes,
            seasons: seasons,
            customPosterUrl: customPosterUrl,
            customBackdropUrl: response.customBackdropUrl ?? response.backdropUrl
        )
    }

    func replacingHasLiked(_ liked: Bool) -> MediaDetail {
        MediaDetail(
            ref: ref,
            title: title,
            subtitle: subtitle,
            overview: overview,
            synopsis: synopsis,
            imageUrl: imageUrl,
            posterUrl: posterUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: posterAccentColor,
            logoUrl: logoUrl,
            logoWidth: logoWidth,
            logoHeight: logoHeight,
            logoAspectRatio: logoAspectRatio,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: (userState ?? UserMediaState(isTracked: false)).replacingHasLiked(liked),
            backdropUrl: backdropUrl,
            details: details,
            related: related,
            providers: providers,
            community: community,
            externalRatings: externalRatings,
            reviews: reviews,
            cast: cast,
            crew: crew,
            relatedSections: relatedSections,
            episodes: episodes,
            seasons: seasons,
            customPosterUrl: customPosterUrl,
            customBackdropUrl: customBackdropUrl
        )
    }

    var displaySynopsis: String? {
        let placeholder = "No synopsis available."
        let candidates = [
            overview,
            synopsis,
            details?["synopsis"]?.stringValue,
            details?["overview"]?.stringValue,
            details?["description"]?.stringValue,
        ]
        for candidate in candidates {
            guard let text = candidate?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !text.isEmpty,
                  text != placeholder else { continue }
            return text
        }
        return nil
    }
}

enum PosterOrientation: String, Codable, Hashable {
    case portrait
    case landscape
    case square
    case unknown

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let value = try container.decode(String.self)
        self = PosterOrientation(rawValue: value) ?? .unknown
    }
}

struct PosterOptionsResponse: Codable, Equatable {
    let posters: [PosterOption]
}

struct BackdropOptionsResponse: Codable, Equatable {
    let backdrops: [PosterOption]
}

struct PosterOption: Codable, Identifiable, Equatable {
    let url: String
    let thumbnailUrl: String?
    let width: Int
    let height: Int
    let aspectRatio: Double?
    let voteAverage: Double
    let voteCount: Int
    let language: String?
    let isOriginal: Bool
    let isSelected: Bool

    var id: String { url }
}

struct PosterSaveRequest: Codable, Equatable {
    let posterUrl: String
}

struct PosterSaveResponse: Codable, Equatable {
    let posterUrl: String
    let customPosterUrl: String?
    let posterAccentColor: String?
}

struct BackdropSaveRequest: Codable, Equatable {
    let backdropUrl: String
}

struct BackdropSaveResponse: Codable, Equatable {
    let backdropUrl: String
    let customBackdropUrl: String?
}

struct HallOfFameItemWriteRequest: Codable, Equatable {
    let ref: MediaRef
}

struct HallOfFameItemsResponse: Codable, Equatable {
    let items: [String: MediaSummary?]
}

struct UserMediaState: Codable, Hashable {
    let isTracked: Bool
    let trackingId: Int?
    let status: String?
    let rating: String?
    let progress: ProgressState?
    let diaryEntryId: Int?
    let diaryCount: Int?
    let diaryRating: String?
    let diaryConsumedAt: String?
    let inLists: [Int]
    let hasLiked: Bool

    enum CodingKeys: String, CodingKey {
        case isTracked
        case trackingId
        case status
        case rating
        case progress
        case diaryEntryId
        case diaryCount
        case diaryRating
        case diaryConsumedAt
        case inLists
        case hasLiked
    }

    init(
        isTracked: Bool,
        trackingId: Int? = nil,
        status: String? = nil,
        rating: String? = nil,
        progress: ProgressState? = nil,
        diaryEntryId: Int? = nil,
        diaryCount: Int? = nil,
        diaryRating: String? = nil,
        diaryConsumedAt: String? = nil,
        inLists: [Int] = [],
        hasLiked: Bool = false
    ) {
        self.isTracked = isTracked
        self.trackingId = trackingId
        self.status = status
        self.rating = rating
        self.progress = progress
        self.diaryEntryId = diaryEntryId
        self.diaryCount = diaryCount
        self.diaryRating = diaryRating
        self.diaryConsumedAt = diaryConsumedAt
        self.inLists = inLists
        self.hasLiked = hasLiked
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        isTracked = try container.decode(Bool.self, forKey: .isTracked)
        trackingId = try container.decodeIfPresent(Int.self, forKey: .trackingId)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        rating = try container.decodeIfPresent(String.self, forKey: .rating)
        progress = try container.decodeIfPresent(ProgressState.self, forKey: .progress)
        diaryEntryId = try container.decodeIfPresent(Int.self, forKey: .diaryEntryId)
        diaryCount = try container.decodeIfPresent(Int.self, forKey: .diaryCount)
        diaryRating = try container.decodeIfPresent(String.self, forKey: .diaryRating)
        diaryConsumedAt = try container.decodeIfPresent(String.self, forKey: .diaryConsumedAt)
        inLists = try container.decodeIfPresent([Int].self, forKey: .inLists) ?? []
        hasLiked = try container.decodeIfPresent(Bool.self, forKey: .hasLiked) ?? false
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(isTracked, forKey: .isTracked)
        try container.encodeIfPresent(trackingId, forKey: .trackingId)
        try container.encodeIfPresent(status, forKey: .status)
        try container.encodeIfPresent(rating, forKey: .rating)
        try container.encodeIfPresent(progress, forKey: .progress)
        try container.encodeIfPresent(diaryEntryId, forKey: .diaryEntryId)
        try container.encodeIfPresent(diaryCount, forKey: .diaryCount)
        try container.encodeIfPresent(diaryRating, forKey: .diaryRating)
        try container.encodeIfPresent(diaryConsumedAt, forKey: .diaryConsumedAt)
        try container.encode(inLists, forKey: .inLists)
        try container.encode(hasLiked, forKey: .hasLiked)
    }

    func replacingHasLiked(_ liked: Bool) -> UserMediaState {
        UserMediaState(
            isTracked: isTracked,
            trackingId: trackingId,
            status: status,
            rating: rating,
            progress: progress,
            diaryEntryId: diaryEntryId,
            diaryCount: diaryCount,
            diaryRating: diaryRating,
            diaryConsumedAt: diaryConsumedAt,
            inLists: inLists,
            hasLiked: liked
        )
    }
}

struct MediaLikeResponse: Codable, Equatable {
    let liked: Bool
    let media: MediaSummary?
}

struct CommunityStats: Codable {
    let averageRating: String?
    let ratingCount: Int
    let diaryCount: Int
    let reviewCount: Int
    let likedCount: Int
    let ratingDistribution: [RatingDistributionBucket]

    enum CodingKeys: String, CodingKey {
        case averageRating
        case ratingCount
        case diaryCount
        case reviewCount
        case likedCount
        case ratingDistribution
    }

    init(
        averageRating: String?,
        ratingCount: Int,
        diaryCount: Int,
        reviewCount: Int,
        likedCount: Int,
        ratingDistribution: [RatingDistributionBucket] = []
    ) {
        self.averageRating = averageRating
        self.ratingCount = ratingCount
        self.diaryCount = diaryCount
        self.reviewCount = reviewCount
        self.likedCount = likedCount
        self.ratingDistribution = ratingDistribution
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        averageRating = try container.decodeIfPresent(String.self, forKey: .averageRating)
        ratingCount = try container.decode(Int.self, forKey: .ratingCount)
        diaryCount = try container.decode(Int.self, forKey: .diaryCount)
        reviewCount = try container.decode(Int.self, forKey: .reviewCount)
        likedCount = try container.decode(Int.self, forKey: .likedCount)
        ratingDistribution = try container.decodeIfPresent([RatingDistributionBucket].self, forKey: .ratingDistribution) ?? []
    }
}

struct RatingDistributionBucket: Codable, Hashable {
    let rating: String
    let count: Int
}

struct ExternalRating: Codable, Identifiable, Hashable {
    let source: String
    let value: String
    let voteCount: Int?
    let maxValue: String?

    var id: String { source }
}

struct MediaReview: Codable, Identifiable, Hashable {
    let id: Int
    let user: UserSummary
    let rating: String?
    let reviewTitle: String?
    let review: String
    let containsSpoilers: Bool
    var likeCount: Int
    var viewerHasLiked: Bool
    let consumedAt: String?
    let createdAt: String?
}

struct LikeState: Codable, Equatable {
    let liked: Bool
    let likeCount: Int
}

struct CreditPerson: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let role: String?
    let character: String?
    let imageUrl: String?
}

struct RelatedMediaSection: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let items: [MediaSummary]
}

struct EpisodeSummary: Codable, Identifiable, Hashable {
    let episodeNumber: Int
    let title: String
    let overview: String?
    let airDate: String?
    let runtime: String?
    let imageUrl: String?
    let rating: String?

    var id: Int { episodeNumber }
}

struct SeasonSummary: Codable, Identifiable, Hashable {
    let seasonNumber: Int
    let title: String
    let episodeCount: Int?
    let imageUrl: String?
    let releaseDate: String?

    var id: Int { seasonNumber }
}

enum JSONValue: Codable, Hashable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value.")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case let .string(value):
            try container.encode(value)
        case let .number(value):
            try container.encode(value)
        case let .bool(value):
            try container.encode(value)
        case let .object(value):
            try container.encode(value)
        case let .array(value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }
}

extension JSONValue {
    var stringValue: String? {
        if case let .string(value) = self {
            return value
        }
        return nil
    }
}
