// Renders Woodshed's app icon (a waveform with a gold loop region) into an .iconset directory.
// Usage: swift packaging/make_icon.swift <output.iconset>
import Cocoa

let output = URL(fileURLWithPath: CommandLine.arguments[1])
try? FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

func render(pixels: Int) -> Data {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4,
                                  hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let size = CGFloat(pixels), inset = size * 0.1
    let tile = NSRect(x: inset, y: inset, width: size - 2 * inset, height: size - 2 * inset)
    let shape = NSBezierPath(roundedRect: tile, xRadius: tile.width * 0.225, yRadius: tile.width * 0.225)
    NSGradient(starting: NSColor(red: 0.16, green: 0.18, blue: 0.21, alpha: 1), ending: NSColor(red: 0.07, green: 0.08, blue: 0.09, alpha: 1))!.draw(in: shape, angle: -90)
    shape.addClip()

    let gold = NSColor(red: 0.85, green: 0.64, blue: 0.25, alpha: 1), grey = NSColor(red: 0.42, green: 0.46, blue: 0.52, alpha: 1)
    let heights: [CGFloat] = [0.18, 0.34, 0.26, 0.52, 0.74, 0.46, 0.62, 0.38, 0.56, 0.30, 0.20, 0.12]
    let loopBars = 3...8
    let barSpacing = tile.width * 0.78 / CGFloat(heights.count), barWidth = barSpacing * 0.56
    let left = tile.minX + tile.width * 0.11, middle = tile.midY

    let loopLeft = left + CGFloat(loopBars.lowerBound) * barSpacing - barSpacing * 0.22
    let loopRight = left + CGFloat(loopBars.upperBound + 1) * barSpacing - barSpacing * 0.22
    gold.withAlphaComponent(0.16).setFill()
    NSRect(x: loopLeft, y: tile.minY, width: loopRight - loopLeft, height: tile.height).fill()
    gold.setFill()
    for edge in [loopLeft, loopRight - tile.width * 0.012] { NSRect(x: edge, y: tile.minY, width: tile.width * 0.012, height: tile.height).fill() }

    for (index, height) in heights.enumerated() {
        (loopBars.contains(index) ? gold : grey).setFill()
        let barHeight = tile.height * height
        let bar = NSRect(x: left + CGFloat(index) * barSpacing, y: middle - barHeight / 2, width: barWidth, height: barHeight)
        NSBezierPath(roundedRect: bar, xRadius: barWidth / 2, yRadius: barWidth / 2).fill()
    }
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}

for (points, scale) in [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)] {
    let name = "icon_\(points)x\(points)\(scale == 2 ? "@2x" : "").png"
    try render(pixels: points * scale).write(to: output.appendingPathComponent(name))
}
