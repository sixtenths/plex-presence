"""Opt-in sharing of the server's actual selected poster through a 1-hour upload."""
import hashlib
import re
import time
import uuid
from urllib.request import Request, build_opener
from .artwork import ArtworkResolver
from .network import NoRedirect


class SelectedArtworkResolver(ArtworkResolver):
    CDN_HOSTS = ArtworkResolver.CDN_HOSTS | {"litter.catbox.moe"}
    SUCCESS_TTL = 300
    MAX_IMAGE = 8 * 1024 * 1024

    def __init__(self, config):
        super().__init__(config)
        self.uploads = {}

    @staticmethod
    def image_type(raw):
        if raw.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", "jpg"
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", "png"
        if raw.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif", "gif"
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            return "image/webp", "webp"
        raise ValueError("Unsupported poster image")

    def _upload(self, raw):
        mime, suffix = self.image_type(raw)
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in (("reqtype", "fileupload"), ("time", "1h")):
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="fileToUpload"; filename="poster.{suffix}"\r\nContent-Type: {mime}\r\n\r\n').encode() + raw + b'\r\n')
        parts.append((f'--{boundary}--\r\n').encode())
        request = Request("https://litterbox.catbox.moe/resources/internals/api.php", data=b"".join(parts),
                          headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            url = response.read(1024).decode("ascii").strip()
        if not re.fullmatch(r"https://litter\.catbox\.moe/[A-Za-z0-9]+\.(jpg|jpeg|png|gif|webp)", url):
            raise ValueError("Invalid poster upload response")
        return url

    def _fetch(self, item_id):
        if not self._opener or not self._token:
            return None
        request = Request(self._base + "/library/metadata/" + item_id + "/thumb",
                          headers={"X-Plex-Token": self._token})
        with self._opener.open(request, timeout=5) as response:
            raw = response.read(self.MAX_IMAGE + 1)
        if len(raw) > self.MAX_IMAGE:
            raise ValueError("Poster image too large")
        self.image_type(raw)
        digest = hashlib.sha256(raw).hexdigest()
        now = time.monotonic()
        self.uploads = {key: value for key, value in self.uploads.items() if value[0] > now}
        cached = self.uploads.get(digest)
        if cached:
            return cached[1]
        url = self._upload(raw)
        if len(self.uploads) >= 128:
            self.uploads.pop(next(iter(self.uploads)))
        self.uploads[digest] = (now + 3000, url)
        return url
