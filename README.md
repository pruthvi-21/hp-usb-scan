# HP USB Scan

Scan from an HP printer plugged into your Mac over USB, then view, crop and export the scans from any phone or computer on the same Wi-Fi. No HP software, drivers, Windows or Linux needed.

## Which printers work

HP all-in-ones that scan over USB with HP's **LEDM** protocol. That covers many HP inkjet all-in-ones from roughly 2010 onwards (DeskJet, DeskJet Ink Advantage, ENVY, OfficeJet, Photosmart lines), especially USB-only models.

- **Tested:** HP DeskJet 2300 series (DeskJet Ink Advantage 2300 All-in-One).
- **Flatbed only:** scans from the glass; document feeders (ADF) aren't supported.
- **Not needed for:** printers that support AirScan/eSCL (most HP printers with Wi-Fi from about 2018 on). macOS can already scan from those with Image Capture.

## Setup

You need a Mac with:

- **Homebrew** ([brew.sh](https://brew.sh)). It's used to install `libusb`, the library that talks to USB devices.
- **Python 3** (`python3 --version` should print 3.10 or newer). macOS offers to install it the first time you run `python3`; `brew install python` works too.

Then:

1. **Get the code** into a folder on the Mac (clone or copy it), and open Terminal in that folder:

   ```sh
   cd ~/Code/products/hp-usb-scan
   ```

2. **Plug the printer into the Mac with a USB cable** and turn it on.

3. **Check that your printer is supported:**

   ```sh
   ./run.sh --check
   ```

   The first run installs `libusb` (via Homebrew) and sets up a private Python environment in `.venv/` with the two packages the app needs (`pyusb`, `Pillow`). Nothing is installed system-wide apart from `libusb`.

   `Compatible: HP DeskJet 2300 series (scanner Idle)` means you're good to go. `Not found` means no compatible HP printer is connected; see [Troubleshooting](#troubleshooting).

## Run

```sh
./run.sh
```

It prints the addresses to open:

```
HP USB Scan is running. On your phone (same Wi-Fi) open:
    http://192.168.1.20:8080
    http://Your-Mac.local:8080
```

- **On your phone:** connect to the same Wi-Fi as the Mac and open the first address (the IP one works on every phone; the `.local` one doesn't on some Androids). Adding it to your home screen makes it feel like an app.
- **On the Mac itself:** open `http://localhost:8080` in Chrome or Edge. Opened this way, exports can ask where to save the file.

Keep the Terminal window open while you use it. **Stop** it with **Ctrl-C**.

The Mac needs to stay awake and plugged into the printer while you scan. Scans are saved in the `scans/` folder next to the code.

To use a different port: `PORT=9000 ./run.sh`.

## Using it

1. Put the page face down on the glass, in the corner marked with the arrow.
2. Pick colour, quality and paper size, then tap **Scan**. The page fills in as it scans; **Stop** cancels.
3. Tap a scan to crop it (drag the corners; hold the eye button to preview the result), rename it, or delete it.
4. Export: hover a scan for **JPEG** or **PDF**, or tap **Select**, pick pages in the order you want them, and **Save as PDF**. The export dialog has a compression slider that shows the exact file size.

## Troubleshooting

- **`Address already in use`:** the app is already running (maybe in another Terminal window). Use that one, or stop it with `pkill -f server.py` and run `./run.sh` again.
- **`Not found` / "No printer" in the app:** check the USB cable and that the printer is on. Only one program can use the printer at a time, so quit other scan tools, and stop the app before running `./run.sh --check`. If it still isn't found, your model probably doesn't use LEDM over USB.
- **The phone can't open the page:** make sure the phone is on the same Wi-Fi (not mobile data), and use the IP address rather than `.local`. If macOS asks whether Python may accept incoming connections, allow it.
- **"Busy" right after a scan:** the scanner head is still returning; it's ready again within a few seconds.
- **Very slow scans:** above 600 dpi a full page can take several minutes. Use Quick (150) or Good (300) for documents.

## Features

Colour or black & white; 75–1200 dpi; A4, Letter, B5, A5, A6, ID card, 4×6 and 5×7 photo sizes, or the whole glass; live progress with the page filling in as it scans, and a stop button; four-corner perspective crop with a magnifier and preview; rename; export to JPEG or multi-page PDF with a compression slider; works from several devices at once.
