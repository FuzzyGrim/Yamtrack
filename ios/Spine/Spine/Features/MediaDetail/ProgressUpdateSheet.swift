import SwiftUI
import UIKit

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

    var keyboardInputText: String {
        hasEditedInput ? input : ""
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
        if !hasEditedInput, digits == input {
            return
        }
        guard !digits.isEmpty else {
            input = ""
            hasEditedInput = true
            return
        }
        if !hasEditedInput {
            let replacement = !input.isEmpty && digits.hasPrefix(input) ? String(digits.dropFirst(input.count)) : digits
            input = replacement.isEmpty ? digits : String(replacement.prefix(limit))
        } else {
            input = digits
        }
        hasEditedInput = true
    }

    @discardableResult
    func applyKeyboardInput(currentText: String, range: NSRange, replacement: String) -> String {
        guard let textRange = Range(range, in: currentText) else {
            return keyboardInputText
        }
        let nextText = currentText.replacingCharacters(in: textRange, with: replacement)
        if !hasEditedInput, replacement.contains(where: \.isNumber) {
            updateInput(replacement)
        } else {
            updateInput(nextText)
        }
        return keyboardInputText
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
    @State private var viewModel: ProgressUpdateViewModel
    @State private var isFullProgressAlertPresented = false
    @State private var isInputActive = true

    let isSaving: Bool
    let errorMessage: String?
    let onSave: (ProgressUpdateSaveRequest) async -> Bool
    let onDismiss: () -> Void
    let onLogFinished: () -> Void

    init(
        detail: MediaDetail,
        progress: ProgressState?,
        isSaving: Bool,
        errorMessage: String?,
        onSave: @escaping (ProgressUpdateSaveRequest) async -> Bool,
        onDismiss: @escaping () -> Void,
        onLogFinished: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: ProgressUpdateViewModel(detail: detail, progress: progress))
        self.isSaving = isSaving
        self.errorMessage = errorMessage
        self.onSave = onSave
        self.onDismiss = onDismiss
        self.onLogFinished = onLogFinished
    }

    var body: some View {
        ZStack {
            Color.black.opacity(0.001)
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture {
                    close()
                }

            ProgressKeyboardPresenter(
                viewModel: viewModel,
                isActive: $isInputActive,
                accessoryHeight: 264,
                accessory: panel
            ) {
                onDismiss()
            }
            .frame(width: 1, height: 1)
            .allowsHitTesting(false)
        }
        .alert("Log as finished?", isPresented: $isFullProgressAlertPresented) {
            Button("Save Progress Only") {
                Task {
                    await save()
                }
            }
            Button("Log Finished") {
                close()
                onLogFinished()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This progress is complete. You can save only progress or open the full log screen.")
        }
    }

    private var panel: some View {
        ProgressUpdatePanel(
            viewModel: viewModel,
            isSaving: isSaving,
            errorMessage: errorMessage,
            onClose: {
                close()
            },
            onSave: saveTapped
        )
    }

    private func saveTapped() {
        guard viewModel.canSave else { return }
        if viewModel.isFullProgress {
            isFullProgressAlertPresented = true
        } else {
            Task {
                await save()
            }
        }
    }

    private func save() async {
        guard let request = viewModel.saveRequest else { return }
        if await onSave(request) {
            close()
        }
    }

    private func close() {
        isInputActive = false
        onDismiss()
    }
}

private struct ProgressUpdatePanel: View {
    let viewModel: ProgressUpdateViewModel
    let isSaving: Bool
    let errorMessage: String?
    let onClose: () -> Void
    let onSave: () -> Void

    var body: some View {
        VStack(spacing: 18) {
            Capsule()
                .fill(.secondary.opacity(0.28))
                .frame(width: 38, height: 5)

            header
            progressSummary
            saveButton
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 18)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .foregroundStyle(.primary)
        .background(Color(uiColor: .secondarySystemBackground))
        .clipShape(UnevenRoundedRectangle(topLeadingRadius: 24, topTrailingRadius: 24))
        .gesture(
            DragGesture(minimumDistance: 12)
                .onEnded { value in
                    if value.translation.height > 28 {
                        onClose()
                    }
                }
        )
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
                onClose()
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
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .foregroundStyle(.primary)
        .background(Color.primary.opacity(0.11), in: Capsule())
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

            Text(viewModel.currentValueText)
                .font(.title3.monospacedDigit().weight(.semibold))
                .foregroundStyle(.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.65)
                .frame(maxWidth: .infinity)
        }
        .frame(maxWidth: .infinity)
        .frame(height: 72)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var saveButton: some View {
        Button(action: onSave) {
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
}

private struct ProgressKeyboardPresenter<Accessory: View>: UIViewRepresentable {
    let viewModel: ProgressUpdateViewModel
    @Binding var isActive: Bool
    let accessoryHeight: CGFloat
    let accessory: Accessory
    let onDidEndEditing: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(viewModel: viewModel, accessory: accessory, accessoryHeight: accessoryHeight, onDidEndEditing: onDidEndEditing)
    }

    func makeUIView(context: Context) -> UITextField {
        let textField = UITextField(frame: .zero)
        textField.alpha = 0.01
        textField.keyboardType = .numberPad
        textField.delegate = context.coordinator
        textField.inputAccessoryView = context.coordinator.accessoryView
        DispatchQueue.main.async {
            if isActive {
                textField.becomeFirstResponder()
            }
        }
        return textField
    }

    func updateUIView(_ textField: UITextField, context: Context) {
        context.coordinator.viewModel = viewModel
        context.coordinator.onDidEndEditing = onDidEndEditing
        context.coordinator.accessoryHeight = accessoryHeight
        context.coordinator.hostingController.rootView = accessory
        context.coordinator.accessoryView.invalidateIntrinsicContentSize()
        if textField.text != viewModel.keyboardInputText {
            textField.text = viewModel.keyboardInputText
        }
        if isActive, !textField.isFirstResponder {
            DispatchQueue.main.async {
                textField.becomeFirstResponder()
            }
        } else if !isActive, textField.isFirstResponder {
            textField.resignFirstResponder()
        }
    }

    final class Coordinator: NSObject, UITextFieldDelegate {
        var viewModel: ProgressUpdateViewModel
        var onDidEndEditing: () -> Void
        var accessoryHeight: CGFloat {
            didSet {
                accessoryView.height = accessoryHeight
            }
        }
        let accessoryView: AccessoryContainerView
        let hostingController: UIHostingController<Accessory>

        init(
            viewModel: ProgressUpdateViewModel,
            accessory: Accessory,
            accessoryHeight: CGFloat,
            onDidEndEditing: @escaping () -> Void
        ) {
            self.viewModel = viewModel
            self.onDidEndEditing = onDidEndEditing
            self.accessoryHeight = accessoryHeight
            self.accessoryView = AccessoryContainerView(height: accessoryHeight)
            self.hostingController = UIHostingController(rootView: accessory)
            super.init()

            hostingController.view.backgroundColor = .secondarySystemBackground
            hostingController.view.translatesAutoresizingMaskIntoConstraints = false
            accessoryView.addSubview(hostingController.view)
            NSLayoutConstraint.activate([
                hostingController.view.leadingAnchor.constraint(equalTo: accessoryView.leadingAnchor),
                hostingController.view.trailingAnchor.constraint(equalTo: accessoryView.trailingAnchor),
                hostingController.view.topAnchor.constraint(equalTo: accessoryView.topAnchor),
                hostingController.view.bottomAnchor.constraint(equalTo: accessoryView.bottomAnchor)
            ])
        }

        func textField(
            _ textField: UITextField,
            shouldChangeCharactersIn range: NSRange,
            replacementString string: String
        ) -> Bool {
            textField.text = viewModel.applyKeyboardInput(
                currentText: textField.text ?? "",
                range: range,
                replacement: string
            )
            return false
        }

        func textFieldDidEndEditing(_ textField: UITextField) {
            onDidEndEditing()
        }
    }
}

private final class AccessoryContainerView: UIView {
    var height: CGFloat {
        didSet {
            invalidateIntrinsicContentSize()
        }
    }

    init(height: CGFloat) {
        self.height = height
        super.init(frame: CGRect(x: 0, y: 0, width: 0, height: height))
        backgroundColor = .secondarySystemBackground
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override var intrinsicContentSize: CGSize {
        CGSize(width: UIView.noIntrinsicMetric, height: height)
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
