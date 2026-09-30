"""Scan from an HP DeskJet 2300 over USB using HP's LEDM (HTTP-over-USB) protocol."""
import math, os, re, struct, sys, time
import usb.core, usb.util, usb.backend.libusb1

HP_VENDOR = 0x03F0
LIBUSB_PATHS = ["/opt/homebrew/lib/libusb-1.0.dylib", "/usr/local/lib/libusb-1.0.dylib"]


def _find_libusb(name):
    return next((p for p in LIBUSB_PATHS if os.path.exists(p)), None)

SCAN_JOB = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanJob xmlns:scan="http://www.hp.com/schemas/imaging/con/cnx/scan/2008/08/19" xmlns:dd="http://www.hp.com/schemas/imaging/con/dictionaries/1.0/">
<scan:XResolution>{dpi}</scan:XResolution>
<scan:YResolution>{dpi}</scan:YResolution>
<scan:XStart>0</scan:XStart>
<scan:YStart>0</scan:YStart>
<scan:Width>{width}</scan:Width>
<scan:Height>{height}</scan:Height>
<scan:Format>Jpeg</scan:Format>
<scan:CompressionQFactor>0</scan:CompressionQFactor>
<scan:ColorSpace>{color}</scan:ColorSpace>
<scan:BitDepth>8</scan:BitDepth>
<scan:InputSource>Platen</scan:InputSource>
<scan:GrayRendering>NTSC</scan:GrayRendering>
<scan:ToneMap><scan:Gamma>1000</scan:Gamma><scan:Brightness>1000</scan:Brightness><scan:Contrast>1000</scan:Contrast><scan:Highlite>179</scan:Highlite><scan:Shadow>25</scan:Shadow></scan:ToneMap>
<scan:ContentType>{content}</scan:ContentType>
</scan:ScanJob>"""

CANCEL_JOB = ('<Job xmlns="http://www.hp.com/schemas/imaging/con/ledm/jobs/2009/04/30" '
              'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><JobState>Canceled</JobState></Job>')

# Page sizes in 1/300 inch
SIZES = {
    "a4": (2480, 3508), "letter": (2550, 3300), "a5": (1748, 2480), "a6": (1240, 1748),
    "b5": (2079, 2953), "photo4x6": (1200, 1800), "photo5x7": (1500, 2100),
    "idcard": (1011, 638), "full": (2550, 3508),  # full = the whole glass
}


class LedmUsb:
    def __init__(self):
        be = usb.backend.libusb1.get_backend(find_library=_find_libusb)
        self.dev = usb.core.find(idVendor=HP_VENDOR, custom_match=self._has_ledm, backend=be)
        if self.dev is None:
            raise RuntimeError("HP scanner not found on USB")
        cfg = self.dev.get_active_configuration()
        self.intf = next(i for i in cfg if (i.bInterfaceClass, i.bInterfaceSubClass) == (0xFF, 0xCC))
        usb.util.claim_interface(self.dev, self.intf.bInterfaceNumber)
        is_bulk = lambda e: usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
        direction = lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
        self.ep_out = next(e for e in self.intf if is_bulk(e) and direction(e) == usb.util.ENDPOINT_OUT)
        self.ep_in = next(e for e in self.intf if is_bulk(e) and direction(e) == usb.util.ENDPOINT_IN)
        self.buf = b""
        self.on_wait = None  # called while waiting for data, e.g. to send a cancel mid-download

    @staticmethod
    def _has_ledm(dev):
        return any((i.bInterfaceClass, i.bInterfaceSubClass) == (0xFF, 0xCC)
                   for cfg in dev for i in cfg)

    def close(self):
        usb.util.release_interface(self.dev, self.intf.bInterfaceNumber)
        usb.util.dispose_resources(self.dev)

    def _fill(self, deadline):
        while time.time() < deadline:
            if self.on_wait:
                self.on_wait()
            try:
                chunk = bytes(self.ep_in.read(65536, timeout=1000))
            except usb.core.USBTimeoutError:
                continue
            if chunk:
                self.buf += chunk
                return
        raise TimeoutError("printer stopped responding")

    def _read_until(self, sep, deadline):
        while sep not in self.buf:
            self._fill(deadline)
        head, self.buf = self.buf.split(sep, 1)
        return head

    def _read_exact(self, n, deadline):
        while len(self.buf) < n:
            self._fill(deadline)
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, method, path, body=b""):
        if isinstance(body, str):
            body = body.encode()
        req = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nUser-Agent: hpscan\r\n"
        if body:
            req += f"Content-Type: text/xml\r\nContent-Length: {len(body)}\r\n"
        self.ep_out.write(req.encode() + b"\r\n" + body, timeout=5000)

    def request(self, method, path, body=b"", timeout=60):
        self.send(method, path, body)
        return self.read_response(timeout)

    def read_response(self, timeout=60, on_data=None):
        deadline = time.time() + timeout
        head = self._read_until(b"\r\n\r\n", deadline).decode(errors="replace")
        status = int(head.split(" ", 2)[1])
        headers = {k.strip().lower(): v.strip() for k, v in
                   (line.split(":", 1) for line in head.split("\r\n")[1:] if ":" in line)}
        if headers.get("transfer-encoding", "").lower() == "chunked":
            data = bytearray()
            while True:
                size = int(self._read_until(b"\r\n", deadline).split(b";")[0], 16)
                if size == 0:
                    self._read_until(b"\r\n", deadline)
                    break
                piece = self._read_exact(size, deadline)
                data += piece
                if on_data:
                    on_data(piece)
                self._read_exact(2, deadline)
            data = bytes(data)
        else:
            data = self._read_exact(int(headers.get("content-length", 0)), deadline)
        return status, headers, data


def scanner_state():
    """Return the scanner state (e.g. "Idle", "BusyWithScanJob")."""
    s = LedmUsb()
    try:
        _, _, data = s.request("GET", "/Scan/Status", timeout=10)
        m = re.search(rb"<ScannerState>(\w+)", data)
        return m.group(1).decode() if m else "Unknown"
    finally:
        s.close()


class Cancelled(Exception):
    pass


class ScanProgress:
    """Live state of a scan: phase, fraction done, and the JPEG bytes received so far.

    The printer's JPEG sets a restart interval of one row of blocks, so every restart
    marker in the stream means one more row of the page has been scanned.
    """

    def __init__(self):
        self.phase = "starting"  # starting -> warming up -> scanning -> done
        self.data = bytearray()
        self.rows = 0
        self.total_rows = None
        self._pos = 0  # where to resume looking for restart markers

    @property
    def fraction(self):
        if not self.total_rows:
            return 0.0
        return min(self.rows / self.total_rows, 1.0)

    def feed(self, piece):
        self.data += piece
        if self.total_rows is None and not self._parse_header():
            return
        d = self.data
        end = len(d) - 1  # leave the last byte in case a marker straddles pieces
        i = d.find(b"\xff", self._pos, end)
        while i != -1:
            if 0xD0 <= d[i + 1] <= 0xD7:
                self.rows += 1
            i = d.find(b"\xff", i + 1, end)
        self._pos = max(end, self._pos)

    def _parse_header(self):
        d, i = self.data, 2
        width = height = hmax = vmax = interval = None
        while i + 4 <= len(d):
            marker, length = d[i + 1], struct.unpack(">H", d[i + 2:i + 4])[0]
            if i + 2 + length > len(d):
                return False
            if marker in (0xC0, 0xC1):
                height, width = struct.unpack(">HH", d[i + 5:i + 9])
                comps = d[i + 9]
                hmax = max(d[i + 11 + 3 * k] >> 4 for k in range(comps))
                vmax = max(d[i + 11 + 3 * k] & 0xF for k in range(comps))
            elif marker == 0xDD:
                interval = struct.unpack(">H", d[i + 4:i + 6])[0]
            elif marker == 0xDA:
                if not (width and interval):
                    self.total_rows = 0  # no restart markers: progress unknown
                    return False
                mcus = math.ceil(width / (8 * hmax)) * math.ceil(height / (8 * vmax))
                self.total_rows = math.ceil(mcus / interval)
                self._pos = i + 2 + length
                return True
            i += 2 + length
        return False


def scan(dpi=300, color=True, size="a4", content="Document", log=print, cancel=None, progress=None):
    """Scan one page and return JPEG bytes.

    Set the `cancel` threading.Event to stop mid-scan. Pass a ScanProgress to watch it live.
    """
    progress = progress or ScanProgress()
    width, height = SIZES[size]
    job = SCAN_JOB.format(dpi=dpi, width=width, height=height,
                          color="Color" if color else "Gray", content=content)
    s = LedmUsb()
    try:
        # Right after a scan the head is still returning; give it a moment
        for _ in range(30):
            _, _, data = s.request("GET", "/Scan/Status")
            state = re.search(rb"<ScannerState>(\w+)", data)
            if not state or state.group(1) == b"Idle":
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"scanner busy: {state.group(1).decode()}")

        status, headers, _ = s.request("POST", "/Scan/Jobs", job)
        if status != 201:
            raise RuntimeError(f"scan job rejected: HTTP {status}")
        job_url = re.sub(r"^https?://[^/]+", "", headers["location"])
        log(f"job created: {job_url}")
        progress.phase = "warming up"

        # Wait for the page to become ready, then download it
        for _ in range(120):
            if cancel and cancel.is_set():
                s.request("PUT", job_url, CANCEL_JOB)
                raise Cancelled()
            _, _, data = s.request("GET", job_url)
            m = re.search(rb"<BinaryURL>([^<]+)</BinaryURL>", data)
            if m and b"ReadyToUpload" in data:
                break
            if b"<j:JobState>Canceled" in data or b"<j:JobState>Aborted" in data:
                raise RuntimeError("scan job was canceled by the printer")
            time.sleep(0.5)
        else:
            raise TimeoutError("scan never became ready")

        log("scanning...")
        progress.phase = "scanning"
        # The image streams while the head moves. On cancel, the printer ends the
        # stream early and then answers the cancel request.
        cancel_sent = False

        def check_cancel():
            nonlocal cancel_sent
            if cancel and cancel.is_set() and not cancel_sent:
                cancel_sent = True
                s.send("PUT", job_url, CANCEL_JOB)

        s.on_wait = check_cancel
        s.send("GET", m.group(1).decode())
        status, _, image = s.read_response(timeout=900, on_data=progress.feed)
        s.on_wait = None
        if cancel_sent:
            s.read_response(timeout=30)
            raise Cancelled()
        if status != 200:
            raise RuntimeError(f"image download failed: HTTP {status}")
        progress.phase = "done"
        return image
    finally:
        s.close()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "scan.jpg"
    dpi = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    img = scan(dpi=dpi)
    open(out, "wb").write(img)
    print(f"saved {out} ({len(img)} bytes)")
