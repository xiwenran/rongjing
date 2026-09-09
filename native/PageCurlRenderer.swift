import CoreGraphics
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers

struct CurlOptions: Decodable {
    let angle: Double?
    let radius: Double?
    let shadowSize: Double?
    let shadowAmount: Double?
    let shadowExtent: Double?
}

struct RenderManifest: Decodable {
    let source: String
    let target: String
    let outputDir: String
    let progress: [Double]
    let width: Int
    let height: Int
    let curl: CurlOptions?

    enum CodingKeys: String, CodingKey {
        case source, target, width, height, curl
        case outputDir = "output_dir"
        case progress
    }
}

struct FrameResult: Encodable {
    let index: Int
    let progress: Double
    let path: String
    let width: Int
    let height: Int
}

struct BatchResult: Encodable {
    let ok: Bool
    let filter: String
    let frames: [FrameResult]
}

struct FilterProbe: Encodable {
    let ok: Bool
    let filter: String
    let inputKeys: [String]

    enum CodingKeys: String, CodingKey {
        case ok, filter
        case inputKeys = "input_keys"
    }
}

enum RendererError: Error, CustomStringConvertible {
    case usage
    case invalidManifest(String)
    case imageLoad(String)
    case filterUnavailable
    case filterOutput(Double)
    case pngWrite(String)

    var description: String {
        switch self {
        case .usage:
            return "用法：PageCurlRenderer <manifest.json>"
        case .invalidManifest(let message):
            return "manifest 无效：\(message)"
        case .imageLoad(let path):
            return "无法读取图片：\(path)"
        case .filterUnavailable:
            return "CIPageCurlWithShadowTransition 与 CIPageCurlTransition 均不可用"
        case .filterOutput(let progress):
            return "Core Image 滤镜在 progress=\(progress) 时没有输出"
        case .pngWrite(let message):
            return "PNG 写入失败：\(message)"
        }
    }
}

private func normalizedImage(path: String, extent: CGRect) throws -> CIImage {
    let url = URL(fileURLWithPath: path)
    guard let loaded = CIImage(contentsOf: url, options: [.applyOrientationProperty: true]) else {
        throw RendererError.imageLoad(path)
    }
    let sourceExtent = loaded.extent
    guard sourceExtent.width > 0, sourceExtent.height > 0 else {
        throw RendererError.imageLoad(path)
    }
    let moved = loaded.transformed(by: CGAffineTransform(
        translationX: -sourceExtent.origin.x,
        y: -sourceExtent.origin.y
    ))
    return moved
        .transformed(by: CGAffineTransform(
            scaleX: extent.width / sourceExtent.width,
            y: extent.height / sourceExtent.height
        ))
        .cropped(to: extent)
}

private func makeFilter() throws -> (CIFilter, String) {
    if let filter = CIFilter(name: "CIPageCurlWithShadowTransition") {
        return (filter, "CIPageCurlWithShadowTransition")
    }
    if let filter = CIFilter(name: "CIPageCurlTransition") {
        return (filter, "CIPageCurlTransition")
    }
    throw RendererError.filterUnavailable
}

private func render(
    source: CIImage,
    target: CIImage,
    extent: CGRect,
    progress: Double,
    filterName: String,
    options: CurlOptions?
) throws -> CIImage {
    let clamped = min(1.0, max(0.0, progress))
    if clamped == 0.0 { return source }
    if clamped == 1.0 { return target }

    guard let filter = CIFilter(name: filterName) else {
        throw RendererError.filterUnavailable
    }
    let inputKeys = Set(filter.inputKeys)
    func setSupported(_ value: Any, _ key: String) {
        if inputKeys.contains(key) {
            filter.setValue(value, forKey: key)
        }
    }
    let paperBack = CIImage(color: CIColor(red: 0.94, green: 0.93, blue: 0.89, alpha: 1.0))
        .cropped(to: extent)
    let shading = CIImage(color: CIColor(red: 0.72, green: 0.72, blue: 0.72, alpha: 1.0))
        .cropped(to: extent)

    setSupported(source, kCIInputImageKey)
    setSupported(target, kCIInputTargetImageKey)
    setSupported(paperBack, "inputBacksideImage")
    setSupported(shading, "inputShadingImage")
    setSupported(CIVector(cgRect: extent), "inputExtent")
    setSupported(clamped, kCIInputTimeKey)
    setSupported(options?.angle ?? (-Double.pi / 4.0), kCIInputAngleKey)
    setSupported(options?.radius ?? max(18.0, Double(extent.width) * 0.12), kCIInputRadiusKey)
    setSupported(options?.shadowSize ?? 0.5, "inputShadowSize")
    setSupported(options?.shadowAmount ?? 0.7, "inputShadowAmount")
    let shadowMargin = max(0.0, options?.shadowExtent ?? 18.0)
    let shadowRect = extent.insetBy(dx: -shadowMargin, dy: -shadowMargin)
    setSupported(CIVector(cgRect: shadowRect), "inputShadowExtent")
    guard let output = filter.outputImage else {
        throw RendererError.filterOutput(clamped)
    }
    return output.cropped(to: extent)
}

private func run() throws {
    guard CommandLine.arguments.count == 2 else { throw RendererError.usage }
    if CommandLine.arguments[1] == "--probe-input-keys" {
        let (filter, filterName) = try makeFilter()
        let payload = FilterProbe(ok: true, filter: filterName, inputKeys: filter.inputKeys.sorted())
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        FileHandle.standardOutput.write(try encoder.encode(payload))
        FileHandle.standardOutput.write(Data("\n".utf8))
        return
    }
    if CommandLine.arguments[1] == "--probe-input-attributes" {
        let (filter, filterName) = try makeFilter()
        var inputs: [String: [String: String]] = [:]
        for key in filter.inputKeys.sorted() {
            let attributes = filter.attributes[key] as? [String: Any] ?? [:]
            inputs[key] = [
                "class": attributes[kCIAttributeClass] as? String ?? "",
                "type": attributes[kCIAttributeType] as? String ?? "",
            ]
        }
        let payload: [String: Any] = [
            "ok": true,
            "filter": filterName,
            "inputs": inputs,
        ]
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        return
    }
    let manifestURL = URL(fileURLWithPath: CommandLine.arguments[1])
    let manifest = try JSONDecoder().decode(RenderManifest.self, from: Data(contentsOf: manifestURL))
    guard manifest.width > 0, manifest.height > 0 else {
        throw RendererError.invalidManifest("width 与 height 必须为正整数")
    }
    guard !manifest.progress.isEmpty else {
        throw RendererError.invalidManifest("progress 不能为空")
    }
    guard manifest.progress.allSatisfy({ $0.isFinite && $0 >= 0.0 && $0 <= 1.0 }) else {
        throw RendererError.invalidManifest("progress 必须全部位于 0...1")
    }

    let (_, filterName) = try makeFilter()
    let extent = CGRect(x: 0, y: 0, width: manifest.width, height: manifest.height)
    let source = try normalizedImage(path: manifest.source, extent: extent)
    let target = try normalizedImage(path: manifest.target, extent: extent)
    let outputURL = URL(fileURLWithPath: manifest.outputDir, isDirectory: true)
    try FileManager.default.createDirectory(at: outputURL, withIntermediateDirectories: true)

    let context = CIContext(options: [.cacheIntermediates: true])
    var frames: [FrameResult] = []
    for (index, progress) in manifest.progress.enumerated() {
        let image = try render(
            source: source,
            target: target,
            extent: extent,
            progress: progress,
            filterName: filterName,
            options: manifest.curl
        )
        let filename = String(format: "frame_%04d.png", index)
        let destination = outputURL.appendingPathComponent(filename)
        guard let rendered = context.createCGImage(image, from: extent) else {
            throw RendererError.pngWrite("CIContext 无法生成第 \(index) 帧 CGImage")
        }
        guard let writer = CGImageDestinationCreateWithURL(
            destination as CFURL,
            UTType.png.identifier as CFString,
            1,
            nil
        ) else {
            throw RendererError.pngWrite("无法创建 PNG writer：\(destination.path)")
        }
        CGImageDestinationAddImage(writer, rendered, nil)
        guard CGImageDestinationFinalize(writer) else {
            throw RendererError.pngWrite("ImageIO 无法完成写入：\(destination.path)")
        }
        frames.append(FrameResult(
            index: index,
            progress: progress,
            path: destination.path,
            width: manifest.width,
            height: manifest.height
        ))
    }

    let payload = BatchResult(ok: true, filter: filterName, frames: frames)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    FileHandle.standardOutput.write(try encoder.encode(payload))
    FileHandle.standardOutput.write(Data("\n".utf8))
}

do {
    try run()
} catch {
    let payload = ["ok": false, "error": String(describing: error)] as [String: Any]
    if let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) {
        FileHandle.standardError.write(data)
        FileHandle.standardError.write(Data("\n".utf8))
    }
    exit(1)
}
