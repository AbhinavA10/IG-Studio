"""IG Studio: a local Instagram carousel editor for folders of DSLR exports.

Run:  python ig_studio.py [trip folder] [--port=5178] [--no-browser]   (needs Pillow)
Then the editor opens at http://localhost:5178/. The same arguments work for IGStudio.exe.

The server reads photos straight from disk, serves cached preview thumbnails to the
browser, and renders exported slides with Pillow into <trip>/_instagram/<post>/.
Layout decisions are made in the browser; export requests carry exact crop boxes,
so the server never has to re-derive the layout.
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from PIL import Image, ImageCms, ImageOps

# Bundled files live in a temporary unpack folder when running as IGStudio.exe.
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
FROZEN = getattr(sys, "frozen", False)
PORT = 5178
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
OUT_DIR = "_instagram"
LAYOUT_FILE = "layout.json"
SLIDE_RE = re.compile(r"^\d\d\.jpg$")
THUMB_MAX = 1600
CACHE_DIR = Path(tempfile.gettempdir()) / "ig-studio-thumbs"
PREFS_FILE = Path.home() / ".ig-studio.json"
ROOT_POST = "."  # post key for photos directly inside the trip folder

SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
SRGB_BYTES = SRGB.tobytes()

INFO_POOL = ThreadPoolExecutor(max_workers=8)  # header reads only; never queued behind thumbnails
THUMB_POOL = ThreadPoolExecutor(max_workers=max(4, (os.cpu_count() or 4) - 2))
EXPORT_POOL = ThreadPoolExecutor(max_workers=os.cpu_count() or 4)
_thumb_jobs = {}
_thumb_lock = threading.Lock()

Image.MAX_IMAGE_PIXELS = 300_000_000  # allow large panoramas


# ---------- Folders ----------

def is_image(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in IMAGE_EXT and not p.name.startswith(".")


def list_images(d: Path):
    return sorted((p for p in d.iterdir() if is_image(p)), key=lambda p: p.name.lower())


def resolve_post(root: str, key: str):
    """Returns (photo folder, output folder) for a post, refusing anything outside root."""
    root_dir = Path(root).resolve()
    if not root_dir.is_dir():
        raise ValueError(f"Not a folder: {root}")
    if key == ROOT_POST:
        src, name = root_dir, root_dir.name
    else:
        if not key or key in ("..", OUT_DIR) or any(c in key for c in "/\\:"):
            raise ValueError(f"Bad post name: {key}")
        src, name = root_dir / key, key
        if not src.is_dir():
            raise ValueError(f"Post folder not found: {key}")
    return src, root_dir / OUT_DIR / name


def photo_path(root: str, key: str, name: str) -> Path:
    src, _ = resolve_post(root, key)
    p = src / name
    if Path(name).name != name or not is_image(p):
        raise ValueError(f"Photo not found: {name}")
    return p


# ---------- Photo metadata ----------

def parse_exif_date(s):
    if not isinstance(s, str):
        return None
    m = re.match(r"(\d{4}):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)", s.strip())
    if not m:
        return None
    try:
        return datetime(*map(int, m.groups())).timestamp()
    except ValueError:
        return None


def photo_info(p: Path):
    st = p.stat()
    with Image.open(p) as im:
        w, h = im.size
        exif = im.getexif()
        if exif.get(274, 1) in (5, 6, 7, 8):
            w, h = h, w
        taken = parse_exif_date(exif.get_ifd(0x8769).get(36867)) or parse_exif_date(exif.get(306))
    return {
        "id": p.name,
        "w": w,
        "h": h,
        "taken": (taken or st.st_mtime) * 1000,
        "cover": bool(re.match(r"(?i)^(cover|!)", p.name)),
        "v": f"{st.st_mtime_ns:x}{st.st_size:x}",
    }


def read_layout(out_dir: Path):
    try:
        return json.loads((out_dir / LAYOUT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def open_trip(root: str):
    root_dir = Path(root).expanduser().resolve()
    if not root_dir.is_dir():
        raise ValueError(f"Not a folder: {root}")
    keys = []
    if list_images(root_dir):
        keys.append(ROOT_POST)
    subdirs = sorted((d for d in root_dir.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))),
                     key=lambda d: natural_key(d.name))
    keys += [d.name for d in subdirs if any(is_image(p) for p in d.iterdir())]

    posts, all_files = [], []
    for key in keys:
        src, out = resolve_post(str(root_dir), key)
        files = list_images(src)
        all_files += files
        posts.append({
            "key": key,
            "name": out.name,
            "photos": [x for x in INFO_POOL.map(safe_info, files) if x],
            "layout": read_layout(out),
            "exported": out.is_dir() and any(SLIDE_RE.match(f.name) for f in out.iterdir()),
        })
    for f in all_files:  # warm the thumbnail cache in the background, first post first
        thumb_job(f)
    remember_root(str(root_dir))
    print(f"Opened {root_dir}: {len(posts)} posts, {len(all_files)} photos")
    return {"root": str(root_dir), "posts": posts}


def safe_info(p: Path):
    try:
        return photo_info(p)
    except Exception as e:  # unreadable file: skip it, keep the rest
        print(f"  skipped {p.name}: {e}")
        return None


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ---------- Image loading ----------

def open_oriented(p: Path, min_size=None) -> Image.Image:
    """Opens a photo upright and in sRGB. min_size=(w, h) lets JPEG decode at a reduced
    scale (1/2, 1/4, 1/8) as long as the result is still at least that big."""
    im = Image.open(p)
    if min_size:
        w, h = min_size
        if im.getexif().get(274, 1) in (5, 6, 7, 8):
            w, h = h, w
        im.draft("RGB", (max(1, int(w)), max(1, int(h))))
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (0, 0, 0))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    if icc:
        try:
            im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)), SRGB, outputMode="RGB")
        except (ImageCms.PyCMSError, OSError):
            pass
    return im


def make_thumb(p: Path) -> bytes:
    st = p.stat()
    key = hashlib.sha1(f"{p}|{st.st_mtime_ns}|{st.st_size}|{THUMB_MAX}".encode()).hexdigest()
    cached = CACHE_DIR / f"{key}.jpg"
    if cached.exists():
        return cached.read_bytes()
    im = open_oriented(p, (THUMB_MAX * 0.75, THUMB_MAX * 0.75))
    im.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    data = buf.getvalue()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, cached)
    return data


def thumb_job(p: Path):
    """One shared future per photo version, so prefetch and browser requests never duplicate work."""
    st = p.stat()
    k = f"{p}|{st.st_mtime_ns}|{st.st_size}"  # a replaced or re-exported file gets a fresh thumbnail
    with _thumb_lock:
        job = _thumb_jobs.get(k)
        if job is None or (job.done() and job.exception()):
            job = _thumb_jobs[k] = THUMB_POOL.submit(make_thumb, p)
        return job


# ---------- Export ----------

def render_slide(src_dir: Path, spec, W: int, H: int, bg) -> Image.Image:
    canvas = Image.new("RGB", (W, H), bg)
    for op in spec["ops"]:
        x, y, w, h = (int(v) for v in op["dest"])
        u0, v0, u1, v1 = op["src"]
        if w <= 0 or h <= 0:
            continue
        p = src_dir / Path(op["name"]).name
        fw, fh = op["full"]  # upright full-resolution size
        # Decode only at the resolution this cell needs.
        scale = max(w / max(1e-6, (u1 - u0) * fw), h / max(1e-6, (v1 - v0) * fh))
        im = open_oriented(p, (fw * scale, fh * scale))
        box = (max(0.0, u0 * im.width), max(0.0, v0 * im.height),
               min(float(im.width), u1 * im.width), min(float(im.height), v1 * im.height))
        canvas.paste(im.resize((w, h), Image.LANCZOS, box=box), (x, y))
        im.close()
    return canvas


def export_post(body):
    src_dir, out_dir = resolve_post(body["root"], body["key"])
    W, H = int(body["width"]), int(body["height"])
    bg = body.get("bg", "#ffffff")
    files = body["files"]
    t0 = time.perf_counter()
    images = list(EXPORT_POOL.map(lambda spec: render_slide(src_dir, spec, W, H, bg), files))

    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.iterdir():
        if SLIDE_RE.match(f.name):
            f.unlink()

    def save(i_im):
        i, im = i_im
        im.save(out_dir / f"{i + 1:02d}.jpg", "JPEG", quality=95, subsampling=0, icc_profile=SRGB_BYTES)

    list(EXPORT_POOL.map(save, enumerate(images)))
    save_layout(body["root"], body["key"], body["layout"])
    ms = round((time.perf_counter() - t0) * 1000)
    print(f"Exported {len(files)} slides to {out_dir} in {ms} ms")
    return {"count": len(files), "dir": str(out_dir), "ms": ms}


def save_layout(root, key, layout):
    _, out_dir = resolve_post(root, key)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (LAYOUT_FILE + ".tmp")
    tmp.write_text(json.dumps(layout, indent=1), encoding="utf-8")
    os.replace(tmp, out_dir / LAYOUT_FILE)
    return {"ok": True}


# ---------- Preferences / native dialogs ----------

def load_prefs():
    try:
        return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember_root(root: str):
    prefs = load_prefs()
    recent = [root] + [r for r in prefs.get("recent", []) if r != root]
    prefs["recent"] = recent[:8]
    try:
        PREFS_FILE.write_text(json.dumps(prefs, indent=1), encoding="utf-8")
    except OSError:
        pass


def folder_dialog(start: str):
    """Runs in a child process (python ig_studio.py --pick-folder) so Tk stays off the server's threads."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    print(filedialog.askdirectory(title="Choose a trip folder", initialdir=start or None) or "")


def pick_folder():
    recent = load_prefs().get("recent", [])
    start = str(Path(recent[0]).parent) if recent else ""
    me = [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]
    r = subprocess.run(me + ["--pick-folder", start], capture_output=True, text=True, timeout=600)
    return {"path": str(Path(r.stdout.strip())) if r.stdout.strip() else ""}


def reveal(root, key):
    _, out_dir = resolve_post(root, key)
    target = out_dir if out_dir.is_dir() else out_dir.parent.parent
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 - local folder chosen by the user
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])
    return {"ok": True}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _allowed(self) -> bool:
        # Only this page may talk to the API (blocks other websites and DNS rebinding).
        ok_hosts = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
        if self.headers.get("Host") not in ok_hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in ok_hosts}

    def _send(self, code, body: bytes, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json", {"Cache-Control": "no-store"})

    def _error(self, e, code=400):
        self._json({"error": str(e)}, code)

    def do_GET(self):
        if not self._allowed():
            return self._send(403, b"Forbidden", "text/plain")
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                return self._send(200, (HERE / "ig-studio.html").read_bytes(), "text/html; charset=utf-8",
                                  {"Cache-Control": "no-store"})
            if url.path == "/api/thumb":
                data = thumb_job(photo_path(q["root"], q["key"], q["name"])).result()
                return self._send(200, data, "image/jpeg", {"Cache-Control": "max-age=31536000, immutable"})
            if url.path == "/api/open":
                return self._json(open_trip(q["root"]))
            if url.path == "/api/recent":
                return self._json({"recent": [r for r in load_prefs().get("recent", []) if Path(r).is_dir()]})
            return self._send(404, b"Not found", "text/plain")
        except (KeyError, ValueError, OSError) as e:
            return self._error(e)

    def do_POST(self):
        if not self._allowed() or not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(403, b"Forbidden", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/export":
                return self._json(export_post(body))
            if path == "/api/layout":
                return self._json(save_layout(body["root"], body["key"], body["layout"]))
            if path == "/api/pick":
                return self._json(pick_folder())
            if path == "/api/reveal":
                return self._json(reveal(body["root"], body["key"]))
            return self._send(404, b"Not found", "text/plain")
        except (KeyError, ValueError, OSError, subprocess.SubprocessError) as e:
            return self._error(e)


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second instance bind a port that is already in use,
    # so a second launch would never notice the first one.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def main():
    global PORT
    args = sys.argv[1:]
    if args[:1] == ["--pick-folder"]:
        return folder_dialog(args[1] if len(args) > 1 else "")
    PORT = next((int(a.split("=", 1)[1]) for a in args if a.startswith("--port=")), PORT)
    open_browser = not any(a in ("--no-browser", "--no-open") for a in args)
    folders = [a for a in args if not a.startswith("--")]

    url = f"http://localhost:{PORT}/"
    if folders:  # e.g. a folder dragged onto the exe
        url += "?root=" + quote(str(Path(folders[0]).resolve()))
    try:
        server = Server(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"IG Studio is already running: {url}")
        if open_browser:
            webbrowser.open(url)
        return
    print(f"IG Studio running at http://localhost:{PORT}/  (close this window to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
