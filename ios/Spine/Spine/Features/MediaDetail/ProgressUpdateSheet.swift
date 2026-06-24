import SwiftUI

struct ProgressUpdateSaveRequest: Equatable {
    let mode: ProgressUpdateMode
    let value: Int
}

@MainActor
@Observable
final class ProgressUpdateViewModel {
    let detail: MediaDetail
    let progress: ProgressState?
    var mode: ProgressUpdateMode
    var input = ""
    private var hasEditedInput = false

    init(detail: MediaDetail, progress: ProgressState?) {
        self.detail = detail
        self.progress = progress
        self.mode = Self.defaultMode(for: detail, progress: progress)
        if let lastValue {
            input = String(lastValue)
        }
    }

    var totalPages: Int? {
        detail.progressTotalPages ?? Self.intValue(progress?.max)
    }

    var lastValue: Int? {
        guard let progress else { return nil }
        return progress.value(in: mode)
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
        guard currentValue != lastValue else {
            guard let value = currentValue ?? lastValue else {
                return mode == .pages ? "Pages read" : "\(mode.title) \(actionVerb)"
            }
            if mode == .percentage {
                return "\(value)% \(actionVerb)"
            }
            let unit = value == 1 ? "page" : "pages"
            return "\(value) \(unit) \(actionVerb)"
        }
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

    func updateInput(_ rawValue: String) {
        let limit = mode == .percentage ? 3 : 6
        let digits = String(rawValue.filter(\.isNumber).prefix(limit))
        guard !digits.isEmpty else {
            input = ""
            hasEditedInput = true
            return
        }
        if !hasEditedInput, digits.count > input.count, let inserted = firstInsertedCharacter(from: input, to: digits) {
            input = String(inserted)
        } else {
            input = digits
        }
        hasEditedInput = true
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

    private func firstInsertedCharacter(from oldValue: String, to newValue: String) -> Character? {
        for (old, new) in zip(oldValue, newValue) where old != new {
            return new
        }
        return newValue.last
    }

    private static func defaultMode(for detail: MediaDetail, progress: ProgressState?) -> ProgressUpdateMode {
        guard detail.ref.mediaType == "book" else { return .percentage }
        if let preferredMode = ProgressDisplayPreferences.mode(for: detail.ref) {
            return preferredMode
        }
        if let progress {
            return progress.mode
        }
        return detail.progressTotalPages == nil ? .percentage : .pages
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
    @FocusState private var isInputFocused: Bool

    let isSaving: Bool
    let errorMessage: String?
    let onSave: (ProgressUpdateSaveRequest) async -> Bool
    let onLogFinished: () -> Void

    init(
        detail: MediaDetail,
        progress: ProgressState?,
        isSaving: Bool,
        errorMessage: String?,
        onSave: @escaping (ProgressUpdateSaveRequest) async -> Bool,
        onLogFinished: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: ProgressUpdateViewModel(detail: detail, progress: progress))
        self.isSaving = isSaving
        self.errorMessage = errorMessage
        self.onSave = onSave
        self.onLogFinished = onLogFinished
    }

    var body: some View {
        VStack(spacing: 18) {
            Capsule()
                .fill(.secondary.opacity(0.28))
                .frame(width: 38, height: 5)
                .padding(.top, 8)

            header
            progressSummary
            saveButton
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 18)
        .foregroundStyle(.primary)
        .presentationBackground(.regularMaterial)
        .presentationCornerRadius(24)
        .onAppear {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                isInputFocused = true
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") {
                    isInputFocused = false
                }
            }
        }
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
                slot: .searchRow,
                mediaType: viewModel.detail.ref.mediaType,
                orientation: viewModel.detail.posterOrientation
            )
            .shadow(color: .black.opacity(0.22), radius: 8, y: 4)

            VStack(alignment: .leading, spacing: 5) {
                Text("Add new progress...")
                    .font(.headline.weight(.semibold))
                    .lineLimit(1)

                progressModeControl
            }

            Spacer()

            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 14, weight: .semibold))
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.circle)
            .controlSize(.regular)
            .accessibilityLabel("Close")
        }
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
                .font(.subheadline.weight(.semibold))
            if showsChevron {
                Image(systemName: "chevron.down")
                    .font(.system(size: 11, weight: .semibold))
            }
        }
        .foregroundStyle(.secondary)
        .contentShape(Rectangle())
        .accessibilityLabel("Progress mode")
        .accessibilityValue(viewModel.mode.title)
    }

    private var progressSummary: some View {
        VStack(spacing: 10) {
            HStack(alignment: .center, spacing: 8) {
                progressStat(title: "Last", value: viewModel.lastValueText)
                currentProgressStat
                progressStat(title: viewModel.totalTitle, value: viewModel.totalValueText)
            }

            if let message = viewModel.validationMessage ?? errorMessage {
                Text(message)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func progressStat(title: String, value: String) -> some View {
        VStack(spacing: 6) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
            Text(value)
                .font(.title3.monospacedDigit().weight(.semibold))
                .foregroundStyle(.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
        }
        .frame(maxWidth: .infinity)
        .frame(height: 72)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var currentProgressStat: some View {
        VStack(spacing: 6) {
            Text("Current")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)

            ZStack {
                Text(viewModel.currentValueText)
                    .font(.title3.monospacedDigit().weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.65)
                    .frame(maxWidth: .infinity)

                TextField("0", text: Binding(
                    get: { viewModel.input },
                    set: { viewModel.updateInput($0) }
                ))
                .keyboardType(.numberPad)
                .focused($isInputFocused)
                .opacity(0.01)
                .accessibilityLabel("Current progress")
                .accessibilityValue(viewModel.currentValueText)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: 72)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .onTapGesture {
            isInputFocused = true
        }
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
                } else {
                    Text("Save Progress")
                        .font(.headline.weight(.semibold))
                }
                Spacer()
            }
            .frame(height: 50)
            .foregroundStyle(viewModel.canSave && !isSaving ? .black : .white.opacity(0.48))
            .background(viewModel.canSave && !isSaving ? Color.white : Color.white.opacity(0.12), in: Capsule())
        }
        .buttonStyle(.plain)
        .disabled(!viewModel.canSave || isSaving)
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
