"""Tiny web app for scanning from the HP DeskJet over USB. Open it from your phone."""
import json, math, os, re, socket, struct, threading, time
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import quote

from PIL import Image

import hpscan

ROOT = os.path.dirname(os.path.abspath(__file__))
SCANS = os.path.join(ROOT, "scans")
ORIGINALS = os.path.join(SCANS, ".originals")  # uncropped copies, for "Restore original"
TITLES = os.path.join(SCANS, ".titles.json")  # user-given names; files keep their scan-<time> names
PORT = int(os.environ.get("PORT", 8080))
NAME_RE = re.compile(r"^scan-[\w-]+\.jpg$")

titles_lock = threading.Lock()
usb_lock = threading.Lock()  # only one USB conversation at a time
cancel_scan = threading.Event()
live = None  # hpscan.ScanProgress of the scan in flight
os.makedirs(ORIGINALS, exist_ok=True)


def jpeg_info(data):
    """Read width, height and color component count from a JPEG's SOF marker."""
    i = 2
    while i < len(data):
        marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h, data[i + 9]
        i += 2 + length
    raise ValueError("not a JPEG")


def jpegs_to_pdf(images, dpi_list):
    """Build a PDF with one JPEG per page, sized to the scan's real dimensions."""
    objs = []
    kids = []
    for data, dpi in zip(images, dpi_list):
        w, h, components = jpeg_info(data)
        pw, ph = w * 72 / dpi, h * 72 / dpi
        img_id, content_id, page_id = len(objs) + 3, len(objs) + 4, len(objs) + 5
        objs.append(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /%s "
                    b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" %
                    (w, h, b"DeviceGray" if components == 1 else b"DeviceRGB", len(data)) + data + b"\nendstream")
        stream = b"q %.2f 0 0 %.2f 0 0 cm /Im0 Do Q" % (pw, ph)
        objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] "
                    b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>" %
                    (pw, ph, img_id, content_id))
        kids.append(b"%d 0 R" % page_id)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), len(kids))] + objs

    out = b"%PDF-1.4\n"
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def jpeg_dpi(data):
    """Read DPI from the JFIF header (the printer writes it); default 300."""
    if data[6:11] == b"JFIF\0" and data[13] == 1:
        return struct.unpack(">H", data[14:16])[0] or 300
    return 300


def list_scans():
    names = sorted((n for n in os.listdir(SCANS) if NAME_RE.match(n)), reverse=True)
    titles = load_titles()
    out = []
    for n in names:
        st = os.stat(os.path.join(SCANS, n))
        out.append({"name": n, "title": title_of(n, titles), "size": st.st_size,
                    "version": int(st.st_mtime * 1000), "crop": read_crop(n)})
    return out


def load_titles():
    try:
        with open(TITLES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def title_of(name, titles=None):
    """The name shown and used for downloads: the user's title, else the file name without .jpg."""
    return (titles if titles is not None else load_titles()).get(name) or name[:-4]


def set_title(name, title):
    """Set (or with an empty title, clear) a scan's display name."""
    with titles_lock:
        titles = load_titles()
        if title:
            titles[name] = title
        else:
            titles.pop(name, None)
        with open(TITLES + ".tmp", "w") as f:
            json.dump(titles, f, indent=1)
        os.replace(TITLES + ".tmp", TITLES)


def clean_title(title):
    """Make a user-typed name safe to use as a download file name."""
    title = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", " ", str(title))
    title = re.sub(r"\.(jpe?g|pdf)$", "", title.strip(), flags=re.I)
    return re.sub(r"\s+", " ", title).strip(" .")[:120]


def attachment(filename):
    """Content-Disposition for a download, keeping non-ASCII names intact."""
    fallback = filename.encode("ascii", "replace").decode().replace("?", "_").replace('"', "")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def read_crop(name):
    """The crop corners applied to a scan (fractions of the original), or None if uncropped.

    Corners are [[x, y], ...] in order top-left, top-right, bottom-right, bottom-left.
    """
    try:
        with open(os.path.join(ORIGINALS, name + ".json")) as f:
            crop = json.load(f)
    except (OSError, ValueError):
        return None
    if "x" in crop:  # older rectangle format
        x0, y0, x1, y1 = crop["x"], crop["y"], crop["x"] + crop["w"], crop["y"] + crop["h"]
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return crop["corners"]


def remove_original(name):
    for p in (os.path.join(ORIGINALS, name), os.path.join(ORIGINALS, name + ".json")):
        if os.path.exists(p):
            os.remove(p)


def solve(a, b):
    """Solve the linear system a·x = b (Gaussian elimination with partial pivoting)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("crop corners are degenerate")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def perspective_coeffs(corners, out_w, out_h):
    """Pillow PERSPECTIVE coefficients mapping the output rectangle onto `corners` in the source."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip([(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)], corners):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); rhs.append(v)
    return solve(rows, rhs)


def crop_scan(name, corners):
    """Crop a scan to the quadrilateral `corners` (fractions, TL/TR/BR/BL), straightening it
    into a rectangle. Keeps the uncropped original so the crop can be changed or undone."""
    path, orig = os.path.join(SCANS, name), os.path.join(ORIGINALS, name)
    # Always crop from the original so repeated crops never compound quality loss
    source = orig if os.path.exists(orig) else path
    with Image.open(source) as im:
        w, h = im.size
        pts = [(min(max(x, 0), 1) * w, min(max(y, 0), 1) * h) for x, y in corners]
        tl, tr, br, bl = pts
        dist = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1])
        out_w = round(max(dist(tl, tr), dist(bl, br)))
        out_h = round(max(dist(tl, bl), dist(tr, br)))
        if out_w < 8 or out_h < 8:
            raise ValueError("crop is too small")
        dpi = im.info.get("dpi", (300, 300))
        rect = (round(tl[0]), round(tl[1]), round(br[0]), round(br[1]))
        is_rect = all(abs(a - b) < 1 for a, b in [
            (tl[0], bl[0]), (tr[0], br[0]), (tl[1], tr[1]), (bl[1], br[1])])
        if is_rect:
            out = im.crop(rect)
        else:
            coeffs = perspective_coeffs(pts, out_w, out_h)
            out = im.transform((out_w, out_h), Image.Transform.PERSPECTIVE, coeffs,
                                                Image.Resampling.BICUBIC)
        out.save(path + ".tmp", "JPEG", quality=95, dpi=dpi)
    if source == path:
        os.replace(path, orig)
    os.replace(path + ".tmp", path)
    with open(orig + ".json", "w") as f:
        json.dump({"corners": [[x / w, y / h] for x, y in pts]}, f)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if not self.path.startswith(("/api/status", "/api/progress", "/api/live.jpg")):
            super().log_message(fmt, *args)

    def send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/api/status":
            if live:
                return self.send(200, {"connected": True, "state": "Scanning"})
            if not usb_lock.acquire(timeout=3):  # another status check or a scan just starting
                return self.send(200, {"connected": True, "state": "Busy"})
            try:
                return self.send(200, {"connected": True, "state": hpscan.scanner_state()})
            except Exception as e:
                return self.send(200, {"connected": False, "error": str(e)})
            finally:
                usb_lock.release()
        if self.path == "/api/progress":
            p = live
            if not p:
                return self.send(200, {"phase": "idle"})
            return self.send(200, {"phase": p.phase, "percent": round(p.fraction * 100, 1),
                                   "bytes": len(p.data)})
        if self.path.startswith("/api/live.jpg"):
            # The partial JPEG so far; browsers draw the rows that have arrived
            p = live
            if not p or not p.data:
                return self.send(404, {"error": "no scan in progress"})
            return self.send(200, bytes(p.data) + b"\xff\xd9", "image/jpeg")
        if self.path == "/api/scans":
            return self.send(200, list_scans())
        m = re.match(r"^/scans/([^/?]+)(?:\?(.*))?$", self.path)
        if m and NAME_RE.match(m.group(1)):
            query = (m.group(2) or "").split("&")
            path = os.path.join(SCANS, m.group(1))
            if "original" in query and os.path.exists(os.path.join(ORIGINALS, m.group(1))):
                path = os.path.join(ORIGINALS, m.group(1))
            if os.path.exists(path):
                download = "download" in query
                extra = {"Content-Disposition": attachment(title_of(m.group(1)) + ".jpg")} if download else {}
                with open(path, "rb") as f:
                    return self.send(200, f.read(), "image/jpeg", extra)
        self.send(404, {"error": "not found"})

    def do_POST(self):
        global live
        if self.path == "/api/scan":
            opts = self.read_json()
            dpi = int(opts.get("dpi", 300))
            size = opts.get("size", "a4")
            if not 75 <= dpi <= 1200 or size not in hpscan.SIZES:
                return self.send(400, {"error": "bad options"})
            if not usb_lock.acquire(timeout=5):
                return self.send(409, {"error": "Scanner is busy"})
            cancel_scan.clear()
            live = hpscan.ScanProgress()
            try:
                img = hpscan.scan(dpi=dpi, color=opts.get("color", True), size=size,
                                  content="Photo" if opts.get("photo") else "Document",
                                  cancel=cancel_scan, progress=live)
            except hpscan.Cancelled:
                return self.send(200, {"cancelled": True})
            except Exception as e:
                return self.send(500, {"error": str(e)})
            finally:
                live = None
                usb_lock.release()
            name = datetime.now().strftime("scan-%Y%m%d-%H%M%S.jpg")
            with open(os.path.join(SCANS, name), "wb") as f:
                f.write(img)
            return self.send(200, {"name": name, "size": len(img)})

        m = re.match(r"^/api/scans/([^/]+)/(crop|restore|title)$", self.path)
        if m and NAME_RE.match(m.group(1)):
            name, action = m.groups()
            if not os.path.exists(os.path.join(SCANS, name)):
                return self.send(404, {"error": "not found"})
            if action == "title":
                title = clean_title(self.read_json().get("title", ""))
                set_title(name, "" if title == name[:-4] else title)
                return self.send(200, {"title": title_of(name)})
            if action == "restore":
                orig = os.path.join(ORIGINALS, name)
                if os.path.exists(orig):
                    os.replace(orig, os.path.join(SCANS, name))
                remove_original(name)
                return self.send(200, {"ok": True})
            box = self.read_json()
            try:
                corners = [[float(x), float(y)] for x, y in box["corners"]]
                if len(corners) != 4:
                    raise ValueError("need 4 corners")
                crop_scan(name, corners)
            except (KeyError, TypeError, ValueError) as e:
                return self.send(400, {"error": str(e)})
            return self.send(200, {"ok": True})

        if self.path == "/api/cancel":
            cancel_scan.set()
            return self.send(200, {"ok": True})

        if self.path == "/api/pdf":
            names = [n for n in self.read_json().get("names", []) if NAME_RE.match(n)]
            images = []
            for n in names:
                with open(os.path.join(SCANS, n), "rb") as f:
                    images.append(f.read())
            if not images:
                return self.send(400, {"error": "no scans selected"})
            pdf = jpegs_to_pdf(images, [jpeg_dpi(d) for d in images])
            fname = (title_of(names[0]) + ".pdf" if len(names) == 1
                     else datetime.now().strftime("scans-%Y%m%d-%H%M%S.pdf"))
            return self.send(200, pdf, "application/pdf", {"Content-Disposition": attachment(fname)})
        self.send(404, {"error": "not found"})

    def do_DELETE(self):
        m = re.match(r"^/api/scans/([^/]+)$", self.path)
        if m and NAME_RE.match(m.group(1)):
            path = os.path.join(SCANS, m.group(1))
            if os.path.exists(path):
                os.remove(path)
                remove_original(m.group(1))
                set_title(m.group(1), "")
                return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    host = socket.gethostname()
    if not host.endswith(".local"):
        host += ".local"
    try:  # the IP works on phones that can't resolve .local names
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.168.0.1", 9))
        ip = probe.getsockname()[0]
    except OSError:
        ip = None
    print("Scanner app running. On your phone (same Wi-Fi) open:")
    if ip:
        print(f"    http://{ip}:{PORT}")
    print(f"    http://{host}:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
