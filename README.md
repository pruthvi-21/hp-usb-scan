# HP USB Scan

Scan from an HP printer plugged into your Mac over USB, then view, crop and export the scans from any phone or computer on the same Wi-Fi. No HP software, drivers, Windows or Linux needed.

## Which printers work

HP all-in-ones that scan over USB with HP's **LEDM** protocol. That covers many HP inkjet all-in-ones from roughly 2010 onwards (DeskJet, DeskJet Ink Advantage, ENVY, OfficeJet, Photosmart lines), especially USB-only models.

- **Tested:** HP DeskJet 2300 series (DeskJet Ink Advantage 2300 All-in-One).
- **Flatbed only:** scans from the glass; document feeders (ADF) aren't supported.
- **Not needed for:** printers that support AirScan/eSCL (most HP printers with Wi-Fi from about 2018 on). macOS can already scan from those with Image Capture.

To check your printer, plug it in and run:

```sh
./run.sh            # first run sets things up; stop it with Ctrl-C
.venv/bin/python hpscan.py --check
```

`Compatible: HP ... (scanner Idle)` means it will work. (Only one program can use the printer at a time, so stop the app before running the check.)

## Run

```sh
./run.sh
```

It prints the address to open on your phone, e.g. `http://192.168.1.20:8080`. Needs Homebrew (for `libusb`) and Python 3. Scans are saved in `scans/` on the Mac.

## Features

Colour or black & white, 75–1200 dpi, A4/Letter/A5/A6/B5/ID card/photo sizes; live progress with the page filling in as it scans, and a stop button; four-corner perspective crop with a magnifier; rename; export to JPEG or multi-page PDF with a compression slider.
