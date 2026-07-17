import base64
import re
import secrets
import time
import warnings
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlparse

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from cloudinary.exceptions import Error as CloudinaryError
from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 25_000_000
CLOUDINARY_PREFIX = "cld"


class UploadError(ValueError):
    pass


def _optimize_webp(data, directory):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                if probe.format not in {"JPEG", "PNG", "WEBP"}:
                    raise UploadError("Only valid JPG, PNG, and WebP images are accepted.")
                probe.verify()
            with Image.open(BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source)
                image.seek(0)
                image.load()
                max_dimension = 2400 if directory == "receipts" else current_app.config["IMAGE_MAX_DIMENSION"]
                image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                has_alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)
                image = image.convert("RGBA" if has_alpha else "RGB")
                output = BytesIO()
                lossless = directory in {"receipts", "settings"}
                image.save(
                    output,
                    format="WEBP",
                    lossless=lossless,
                    quality=current_app.config["IMAGE_WEBP_QUALITY"],
                    method=6,
                    exact=has_alpha,
                )
                optimized = output.getvalue()
    except UploadError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise UploadError("Only valid JPG, PNG, and WebP images are accepted.") from None
    if not optimized or len(optimized) > current_app.config["MAX_CONTENT_LENGTH"]:
        raise UploadError("The optimized image is still too large. Choose a smaller image.")
    return optimized


def _storage_backend():
    backend = current_app.config["IMAGE_STORAGE_BACKEND"]
    if backend not in {"auto", "local", "cloudinary"}:
        raise UploadError("IMAGE_STORAGE_BACKEND must be auto, local, or cloudinary.")
    if backend == "auto":
        return "cloudinary" if current_app.config["CLOUDINARY_URL"] else "local"
    if backend == "cloudinary" and not current_app.config["CLOUDINARY_URL"]:
        raise UploadError("Cloudinary storage is enabled but CLOUDINARY_URL is missing.")
    return backend


def _configure_cloudinary():
    parsed = urlparse(current_app.config["CLOUDINARY_URL"])
    if parsed.scheme != "cloudinary" or not parsed.hostname or not parsed.username or not parsed.password:
        raise UploadError("CLOUDINARY_URL is invalid or incomplete.")
    cloudinary.config(
        cloud_name=parsed.hostname,
        api_key=unquote(parsed.username),
        api_secret=unquote(parsed.password),
        secure=True,
    )


def _folder(directory):
    root = current_app.config["CLOUDINARY_FOLDER"].strip("/") or "sulitshelf"
    root = "/".join(part for part in root.split("/") if re.fullmatch(r"[A-Za-z0-9_-]+", part))
    return f"{root or 'sulitshelf'}/{directory}"


def _encode_cloudinary_key(delivery_type, image_format, public_id):
    encoded = base64.urlsafe_b64encode(public_id.encode()).decode().rstrip("=")
    key = f"{CLOUDINARY_PREFIX}:{delivery_type}:{image_format}:{encoded}"
    if len(key) > 160:
        raise UploadError("The Cloudinary storage key is too long.")
    return key


def _decode_cloudinary_key(name):
    if not name or not name.startswith(f"{CLOUDINARY_PREFIX}:"):
        return None
    try:
        prefix, delivery_type, image_format, encoded = name.split(":", 3)
        if prefix != CLOUDINARY_PREFIX or delivery_type not in {"upload", "authenticated"} or image_format != "webp":
            return None
        public_id = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        if not public_id or not re.fullmatch(r"[A-Za-z0-9_/-]+", public_id):
            return None
        return delivery_type, image_format, public_id
    except (ValueError, UnicodeDecodeError):
        return None


def save_image(file, directory, prefix, private=False):
    if not file or not file.filename:
        raise UploadError("Choose an image to upload.")
    data = file.read(current_app.config["MAX_CONTENT_LENGTH"] + 1)
    if not data or len(data) > current_app.config["MAX_CONTENT_LENGTH"]:
        raise UploadError("Upload an image up to 8 MB.")
    optimized = _optimize_webp(data, directory)
    token = secrets.token_hex(16)
    if _storage_backend() == "local":
        name = f"{prefix}-{token}.webp"
        target = Path(current_app.config["UPLOAD_ROOT"]) / directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(optimized)
        return name

    _configure_cloudinary()
    delivery_type = "authenticated" if private else "upload"
    try:
        result = cloudinary.uploader.upload(
            BytesIO(optimized),
            resource_type="image",
            type=delivery_type,
            folder=_folder(directory),
            public_id=f"{prefix}-{token}",
            format="webp",
            overwrite=False,
            unique_filename=False,
            use_filename=False,
        )
        public_id = result.get("public_id", "")
        if not public_id:
            raise CloudinaryError("Upload returned no public ID")
        return _encode_cloudinary_key(delivery_type, "webp", public_id)
    except CloudinaryError:
        current_app.logger.exception("Cloudinary image upload failed")
        raise UploadError("The image could not be stored in Cloudinary. Try again.") from None


def media_url(name, private=False, expires_in=300):
    parsed = _decode_cloudinary_key(name)
    if not parsed:
        return None
    delivery_type, image_format, public_id = parsed
    if delivery_type == "authenticated" and not private:
        return None
    _configure_cloudinary()
    if delivery_type == "authenticated":
        return cloudinary.utils.private_download_url(
            public_id,
            image_format,
            resource_type="image",
            type="authenticated",
            expires_at=int(time.time()) + expires_in,
            attachment=False,
        )
    return cloudinary.utils.cloudinary_url(
        public_id,
        resource_type="image",
        type="upload",
        format=image_format,
        secure=True,
    )[0]


def delete_file(directory, name):
    if not name:
        return
    parsed = _decode_cloudinary_key(name)
    if parsed:
        delivery_type, _image_format, public_id = parsed
        try:
            _configure_cloudinary()
            cloudinary.uploader.destroy(public_id, resource_type="image", type=delivery_type, invalidate=True)
        except (CloudinaryError, UploadError):
            current_app.logger.exception("Cloudinary image deletion failed for %s", directory)
        return
    root = (Path(current_app.config["UPLOAD_ROOT"]) / directory).resolve()
    target = (root / Path(name).name).resolve()
    if target.parent == root:
        target.unlink(missing_ok=True)


def path_for(directory, name):
    if _decode_cloudinary_key(name):
        return None
    root = (Path(current_app.config["UPLOAD_ROOT"]) / directory).resolve()
    target = (root / Path(name).name).resolve()
    if target.parent != root or not target.is_file():
        return None
    return target
