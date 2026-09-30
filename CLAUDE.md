# Printer scanner

A local web app for scanning from a USB-connected **HP DeskJet 2300** (print is broken, scanning works) from a phone. The Mac is the bridge: it talks to the scanner over USB and serves a page to phones on the same Wi-Fi.

## Run

```sh
./run.sh   # creates .venv, installs requirements when they change, starts on :8080
```

It prints the LAN URL (IP and `.local`; some Android phones can't resolve `.local`). Only one process may hold the USB interface, so stop the server (`pkill -f server.py`) before running `hpscan.py` or other USB scripts directly.

## Files

- `hpscan.py`: USB transport and scanning. No web code.
- `server.py`: stdlib `ThreadingHTTPServer`. JSON API, crop (Pillow), and PDF building (hand-written, no library).
- `index.html`: the whole UI in one file (vanilla JS, no build step).
- `scans/`: the user's scanned documents (gitignored; personal). `scans/.originals/` holds uncropped originals plus `<name>.json` crop corners. `scans/.titles.json` maps file names to user-given display names; renaming never renames files (they stay `scan-<time>.jpg` so ordering and URLs don't change), but downloads and PDFs use the display name.

## How the scanner is driven (non-obvious)

- The printer has **no IPP-over-USB/eSCL** interface (no `7/1/4`), so Apple Image Capture, HP Smart on Mac, and Docker/Linux `ipp-usb` routes don't work. Docker on macOS can't pass USB through anyway.
- It speaks **HP LEDM**: plain HTTP/1.1 over bulk endpoints of USB interface `FF/CC/00` (interface 0), found via the `LEDMDIS` field of the IEEE 1284 device ID. Interfaces `FF/04/01` accept writes but never answer.
- Scan flow: `GET /Scan/Status` (wait for `Idle`) → `POST /Scan/Jobs` (ScanJob XML, sizes in 1/300 in) → poll job URL until `PageState` `ReadyToUpload` → `GET` the `BinaryURL` (chunked JPEG that streams while the head moves).
- **Cancel**: `PUT` the job URL with a `<JobState>Canceled</JobState>` Job XML *while the image is still streaming*. The printer ends the stream early, then answers the PUT. `LedmUsb.on_wait` exists for this.
- **Progress**: the JPEG sets a restart interval of one MCU row, so counting `FFD0–FFD7` markers gives exact progress (`ScanProgress`). `/api/live.jpg` serves the partial JPEG for the live preview.
- The printer accepts **any dpi from 75 to 1200** even though ScanCaps lists only 75/100/200/300/600/1200. It resamples in firmware.
- It returns **503** to a new job while the head is still returning after a scan, so `scan()` waits for `Idle` first.
- Timing: A4 takes about 8 s at 300 dpi and about 70 s at 600 dpi. The warm-up before data flows is about 4 s.

## Crop

Crops are four corners (TL, TR, BR, BL fractions). The server always crops from the saved original: an exact crop if the corners form an axis-aligned rectangle, otherwise a perspective warp (`perspective_coeffs` solves the 8×8 system without numpy). Output size = the longest opposite edges. The client's `size()` must match this formula.

The crop preview (eye button) is drawn on a canvas by inverse-mapping each output pixel through the same homography. Don't switch it to a CSS `matrix3d` warp: the math is right, but Chrome rasterizes large perspective layers in approximate tiles, which skews text and drops chunks of the page.

Save only applies the crop (no download); JPEG/PDF export lives on the tile hover actions.

## UI conventions

- The design matches the user's **Libreel** app (`~/Code/products/libreel/mobile/design`): "boxy" Material 3, 2px corners on controls and 4px on surfaces, flat toggle track with a filled primary pill, pure black dark mode. **Dark mode only** (no light theme), with Google blue as the primary (`#4285f4`, white text on it). Stop is a red outline, not a fill, so it doesn't compete with the Scan button. Colors are CSS tokens on `:root`.
- At ≥ 840px width: controls on the left, all scans in a large grid on the right. Below that, scans sit under the controls. Opening a scan (or the in-progress tile) shows the viewer with the cropper: on wide screens it replaces only the scans column so the controls stay usable; on small screens it covers the whole screen. Esc closes it.
- Keep it dependency-free on the front end, and keep the Python side to `pyusb` + `Pillow`.

## Git

- Never add Claude as a co-author: no `Co-Authored-By: Claude` trailer (or any other AI attribution line) in commit messages or PR descriptions.

## Testing

- There's no test suite. Verify against the real printer: scans move the head and read whatever is on the glass (often the user's personal documents), so delete test scans afterwards and don't describe their contents.
- UI checks: the in-app browser at a phone size (375×812) and a wide size (1280×800), in light and dark.
