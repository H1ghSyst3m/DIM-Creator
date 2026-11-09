# DIM Creator v3 · Mini Brand Guidelines

**Updated 2026-11-04** · Approved aqua and turquoise mark on a transparent background.

## 1. Logo and Identity

Use **the mark on its own** as the primary brand asset. Keep it free of lettering and background tiles. Use the same shape on Windows, macOS, and Linux. The platform-specific files are **exports of the same mark**.

## 2. Color

These HEX values approximate colors in the approved PNG. The original uses gradients and blended transitions.

| Name | HEX | Use |
|---|---|---|
| Aqua | `#1CECF5` | Bright highlight in the mark |
| Turquoise | `#04AABB` | Primary midtone |
| Mint | `#17DCAD` | Connecting areas and a secondary accent |
| Deep Blue | `#02738F` | Depth and contrast in the mark |
| Green | `#5EF198` | Second bright accent |

For presentations, use `#101820` for dark backgrounds and `#F5F8FA` for light backgrounds. Keep the mark itself transparent.

## 3. Sizes and Clear Space

- The 1024×1024 RGBA master is intended for sRGB and has a transparent background.
- Use a multi-resolution ICO on Windows, a classic ICNS on macOS, and PNGs on Linux for app and installer icons.
- At 16–32 px, use the **optically scaled** exports. These preserve the mark's proportions with slightly less padding.
- As design guidance, leave at least 10% clear space **outside** the visible edge of the mark before placing adjacent elements.
- Test small icons on the target operating system. If the mark loses too much detail, get approval before preparing a separate version optimized for small sizes.

## 4. Usage

**Do**

- Use the approved silhouette as supplied.
- Scale proportionally and leave enough clear space.
- Preserve transparency.
- Test in light and dark modes and on the taskbar and Dock.
- Use the same file for the app and installer.

**Avoid**

- Adding the words “DIM Creator” to the mark.
- Adding arrows, rings, tiles, outlines, or shipping boxes.
- Stretching, tilting, rotating, or mirroring the mark.
- Introducing arbitrary new gradient colors.
- Using an opaque JPEG for the app icon.

## 5. Technical Exports

| Platform or Use | File | Notes |
|---|---|---|
| Windows app / NSIS | `tauri/src-tauri/icons/icon.ico` | Multi-resolution ICO, 16/24/32/48/64/128/256 |
| macOS | `tauri/src-tauri/icons/icon.icns` | Classic ICNS with multiple resolutions |
| Linux | `linux/hicolor/*/apps/dim-creator.png` | PNGs organized in the Hicolor directory structure |
| Tauri | `tauri/src-tauri/icons/` | Standard filenames and directory structure |
| Website | `web/favicon.ico` / `favicon-32x32.png` | Mark only |
| Windows/Linux tray | `tray/color/tray-24.png` | Use when the app has a tray icon |
| macOS tray | `tray/macos/trayTemplate.png`, `trayTemplate@2x.png` | Template icon with system tinting |
| Installer | `installer/nsis/installer.ico` | Same brand mark as the app |

## 6. Platform Guidance

Review the finished app against the current platform design guidelines, including its ICO or ICNS file. Before releasing on macOS, check the icon in the Dock and Finder, verify system masking, and review it in Icon Composer where applicable. Keep the standalone brand asset transparent and use the approved artwork as supplied.

## 7. Source

The exports use the approved `DIM-Creator-Logo-v3/icon.png` as their source. The color assets retain the original colors at each export size. The monochrome tray assets use the source image's alpha silhouette.
