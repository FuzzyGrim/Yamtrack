import SwiftUI

enum ProgressUpdateMode: String, CaseIterable, Identifiable {
    case pages
    case percentage

    var id: String { rawValue }

    var title: String {
        switch self {
        case .pages: "Pages"
        case .percentage: "Percent"
        }
    }

    var apiValue: String {
        switch self {
        case .pages: "pages"
        case .percentage: "percentage"
        }
    }
}

struct ProgressUpdateSaveRequest: Equatable {
    let mode: ProgressUpdateMode
    let value: Int
}

@MainActor
@Observable
final class ProgressUpdateViewModel {
    let detail: MediaDetail
    let tracking: TrackingState?
    var mode: ProgressUpdateMode
    var input = ""

    init(detail: MediaDetail, tracking: TrackingState?) {
        self.detail = detail
        self.tracking = tracking
        self.mode = Self.defaultMode(for: detail, tracking: tracking)
    }

    var totalPages: Int? {
        detail.progressTotalPages ?? Self.intValue(tracking?.progress?.max)
    }

    var lastValue: Int? {
        guard let progress = tracking?.progress, let value = Self.intValue(progress.value) else { return nil }
        return converted(value, from: Self.mode(for: progress), to: mode)
    }

    var currentValue: Int? {
        Int(input)
    }

    var totalTitle: String {
        detail.ref.mediaType == "book" ? "Total Pages" : "Total"
    }

    var totalValueText: String {
        if detail.ref.mediaType == "book" {
            return totalPages.map(String.init) ?? "--"
        }
        return "100%"
    }

    var lastValueText: String {
        valueText(lastValue)
    }

    var currentValueText: String {
        valueText(currentValue)
    }

    var deltaText: String {
        guard let currentValue else {
            return mode == .pages ? "Pages read" : "\(mode.title) \(actionVerb)"
        }
        let delta = currentValue - (lastValue ?? 0)
        let prefix = delta > 0 ? "+" : ""
        if mode == .percentage {
            return "\(prefix)\(delta)% \(actionVerb)"
        }
        let unit = abs(delta) == 1 ? "page" : "pages"
        return "\(prefix)\(delta) \(unit) \(actionVerb)"
    }

    var validationMessage: String? {
        guard let value = currentValue else { return nil }
        if mode == .percentage, value > 100 {
            return "Enter a value from 0 to 100."
        }
        if mode == .pages, let totalPages, value > totalPages {
            return "Enter a value from 0 to \(totalPages)."
        }
        return nil
    }

    var canSave: Bool {
        guard currentValue != nil, validationMessage == nil else { return false }
        return currentValue != lastValue
    }

    var isFullProgress: Bool {
        guard let currentValue else { return false }
        if mode == .percentage {
            return currentValue == 100
        }
        return totalPages.map { currentValue == $0 } ?? false
    }

    var saveRequest: ProgressUpdateSaveRequest? {
        guard canSave, let currentValue else { return nil }
        return ProgressUpdateSaveRequest(mode: mode, value: currentValue)
    }

    func selectMode(_ newMode: ProgressUpdateMode) {
        guard detail.ref.mediaType == "book", newMode != mode else { return }
        if let currentValue {
            input = converted(currentValue, from: mode, to: newMode).map(String.init) ?? ""
        }
        mode = newMode
    }

    func appendDigit(_ digit: Int) {
        guard (0...9).contains(digit), input.count < 6 else { return }
        if input == "0" {
            input = String(digit)
        } else {
            input.append(String(digit))
        }
    }

    func deleteDigit() {
        guard !input.isEmpty else { return }
        input.removeLast()
    }

    private var actionVerb: String {
        detail.ref.mediaType == "game" ? "played" : "read"
    }

    private func valueText(_ value: Int?) -> String {
        guard let value else { return "--" }
        if mode == .percentage {
            return "\(value)%"
        }
        return String(value)
    }

    private func converted(_ value: Int, from: ProgressUpdateMode, to: ProgressUpdateMode) -> Int? {
        guard from != to else { return value }
        guard let totalPages, totalPages > 0 else { return nil }
        switch (from, to) {
        case (.pages, .percentage):
            return Int((Double(value) / Double(totalPages) * 100).rounded())
        case (.percentage, .pages):
            return Int((Double(value) / 100 * Double(totalPages)).rounded())
        default:
            return value
        }
    }

    private static func defaultMode(for detail: MediaDetail, tracking: TrackingState?) -> ProgressUpdateMode {
        guard detail.ref.mediaType == "book" else { return .percentage }
        if let progress = tracking?.progress {
            return mode(for: progress)
        }
        return detail.progressTotalPages == nil ? .percentage : .pages
    }

    private static func mode(for progress: ProgressState) -> ProgressUpdateMode {
        let kind = progress.kind.lowercased()
        let unit = progress.unit.lowercased()
        if kind.contains("percent") || unit.contains("percent") || unit == "%" {
            return .percentage
        }
        return .pages
    }

    private static func intValue(_ value: Decimal?) -> Int? {
        guard let value else { return nil }
        return Int(NSDecimalNumber(decimal: value).doubleValue.rounded())
    }
}

struct ProgressUpdateSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: ProgressUpdateViewModel
    @State private var isFullProgressAlertPresented = false

    let isSaving: Bool
    let errorMessage: String?
    let onSave: (ProgressUpdateSaveRequest) async -> Bool
    let onLogFinished: () -> Void

    init(
        detail: MediaDetail,
        tracking: TrackingState?,
        isSaving: Bool,
        errorMessage: String?,
        onSave: @escaping (ProgressUpdateSaveRequest) async -> Bool,
        onLogFinished: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: ProgressUpdateViewModel(detail: detail, tracking: tracking))
        self.isSaving = isSaving
        self.errorMessage = errorMessage
        self.onSave = onSave
        self.onLogFinished = onLogFinished
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().background(.white.opacity(0.08))
            progressSummary
                .padding(.top, 34)
                .padding(.horizontal, 26)
            saveButton
                .padding(.horizontal, 16)
                .padding(.top, 28)
            keypad
                .padding(.top, 30)
                .padding(.horizontal, 10)
                .padding(.bottom, 24)
        }
        .foregroundStyle(.white)
        .background(Color(red: 0.08, green: 0.075, blue: 0.07))
        .presentationBackground(Color(red: 0.08, green: 0.075, blue: 0.07))
        .presentationCornerRadius(28)
        .alert("Log as finished?", isPresented: $isFullProgressAlertPresented) {
            Button("Save Progress Only") {
                Task {
                    await save()
                }
            }
            Button("Log Finished") {
                dismiss()
                onLogFinished()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This progress is complete. You can save only progress or open the full log screen.")
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            MediaArtwork(
                url: viewModel.detail.displayPosterURL,
                title: viewModel.detail.title,
                slot: .libraryRow,
                mediaType: viewModel.detail.ref.mediaType,
                orientation: viewModel.detail.posterOrientation
            )

            VStack(alignment: .leading, spacing: 6) {
                Text("Add new progress...")
                    .font(.system(size: 18, weight: .heavy))
                    .lineLimit(1)

                progressModeControl
            }

            Spacer()

            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 20, weight: .bold))
                    .foregroundStyle(.white.opacity(0.72))
                    .frame(width: 44, height: 44)
                    .background(.white.opacity(0.08), in: Circle())
                    .overlay {
                        Circle().stroke(.white.opacity(0.12), lineWidth: 1)
                    }
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 18)
    }

    @ViewBuilder
    private var progressModeControl: some View {
        if viewModel.detail.ref.mediaType == "book" {
            Menu {
                ForEach(ProgressUpdateMode.allCases) { mode in
                    Button(mode.title) {
                        viewModel.selectMode(mode)
                    }
                }
            } label: {
                modeLabel(showsChevron: true)
            }
        } else {
            modeLabel(showsChevron: false)
        }
    }

    private func modeLabel(showsChevron: Bool) -> some View {
        HStack(spacing: 6) {
            Text(viewModel.deltaText)
                .font(.system(size: 22, weight: .heavy))
            if showsChevron {
                Image(systemName: "chevron.down")
                    .font(.system(size: 14, weight: .heavy))
            }
        }
        .foregroundStyle(.white)
        .contentShape(Rectangle())
        .accessibilityLabel("Progress mode")
        .accessibilityValue(viewModel.mode.title)
    }

    private var progressSummary: some View {
        VStack(spacing: 14) {
            HStack(alignment: .center) {
                progressStat(title: "Last", value: viewModel.lastValueText, highlighted: false)
                Spacer()
                progressStat(title: "Current", value: viewModel.currentValueText, highlighted: true)
                Spacer()
                progressStat(title: viewModel.totalTitle, value: viewModel.totalValueText, highlighted: false)
            }

            if let message = viewModel.validationMessage ?? errorMessage {
                Text(message)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.red.opacity(0.95))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 8)
            }
        }
    }

    private func progressStat(title: String, value: String, highlighted: Bool) -> some View {
        VStack(spacing: 8) {
            Text(title)
                .font(.system(size: 17, weight: .heavy))
                .foregroundStyle(.white)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
            Text(value)
                .font(.system(size: highlighted ? 32 : 28, weight: .heavy))
                .foregroundStyle(highlighted ? .white : .white.opacity(0.46))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(width: highlighted ? 128 : 92, height: 86)
        .background(highlighted ? .white.opacity(0.12) : .clear, in: RoundedRectangle(cornerRadius: 14))
    }

    private var saveButton: some View {
        Button {
            guard viewModel.canSave else { return }
            if viewModel.isFullProgress {
                isFullProgressAlertPresented = true
            } else {
                Task {
                    await save()
                }
            }
        } label: {
            HStack {
                Spacer()
                if isSaving {
                    ProgressView()
                        .tint(.black)
                } else {
                    Text("Add Progress")
                        .font(.system(size: 17, weight: .heavy))
                }
                Spacer()
            }
            .foregroundStyle(.black)
            .frame(height: 54)
            .background(viewModel.canSave && !isSaving ? .white.opacity(0.9) : .white.opacity(0.38), in: RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .disabled(!viewModel.canSave || isSaving)
    }

    private var keypad: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: 3), spacing: 12) {
            ForEach(1...9, id: \.self) { digit in
                keypadButton(digit)
            }
            Color.clear
                .frame(height: 54)
            keypadButton(0)
            Button {
                viewModel.deleteDigit()
            } label: {
                Image(systemName: "delete.left")
                    .font(.system(size: 26, weight: .medium))
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity)
                    .frame(height: 54)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Delete")
        }
    }

    private func keypadButton(_ digit: Int) -> some View {
        Button {
            viewModel.appendDigit(digit)
        } label: {
            VStack(spacing: 2) {
                Text(String(digit))
                    .font(.system(size: 31, weight: .medium))
                if let letters = keypadLetters[digit] {
                    Text(letters)
                        .font(.system(size: 11, weight: .heavy))
                        .tracking(2)
                }
            }
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .frame(height: 54)
            .background(.white.opacity(0.36), in: RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(String(digit))
    }

    private var keypadLetters: [Int: String] {
        [
            2: "ABC",
            3: "DEF",
            4: "GHI",
            5: "JKL",
            6: "MNO",
            7: "PQRS",
            8: "TUV",
            9: "WXYZ",
        ]
    }

    private func save() async {
        guard let request = viewModel.saveRequest else { return }
        if await onSave(request) {
            dismiss()
        }
    }
}

extension MediaDetail {
    var progressTotalPages: Int? {
        for key in ["number_of_pages", "pages", "total_pages"] {
            if let value = details?[key]?.progressIntValue {
                return value
            }
        }
        return nil
    }
}

private extension JSONValue {
    var progressIntValue: Int? {
        switch self {
        case let .number(value):
            Int(value)
        case let .string(value):
            Int(value)
        default:
            nil
        }
    }
}
