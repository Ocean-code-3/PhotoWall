import os
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import abort, Flask, flash, redirect, render_template, request, send_from_directory, url_for
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename


app = Flask(__name__)
data_dir = Path(os.environ.get("DATA_DIR", app.root_path))
app.config["DATABASE"] = os.environ.get("DATABASE_PATH", str(data_dir / "photowall.db"))
app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", str(data_dir / "uploads"))
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY", "photo-wall-dev")

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
DISPLAY_IMAGE_MAX_WIDTH = 2000
DISPLAY_IMAGE_QUALITY = 82
COLLECTION_DEFINITIONS = [
    {
        "slug": "landscape",
        "title": "LANDSCAPE",
        "description": "Mountains, water, weather, and the quiet distance between them.",
    },
    {
        "slug": "city",
        "title": "CITY",
        "description": "Street corners, passing light, and small moments from daily life.",
    },
]


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def upload_path():
    return Path(app.config["UPLOAD_FOLDER"])


def display_path():
    return upload_path() / "display"


def database_path():
    return Path(app.config["DATABASE"])


def get_db_connection():
    connection = sqlite3.connect(database_path())
    connection.row_factory = sqlite3.Row
    return connection


def ensure_database(import_uploads=True):
    database_path().parent.mkdir(exist_ok=True)
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL UNIQUE,
                display_filename TEXT,
                title TEXT NOT NULL,
                description TEXT,
                location TEXT,
                shot_date TEXT,
                tags TEXT,
                created_at TEXT NOT NULL,
                is_carousel INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        columns = connection.execute("PRAGMA table_info(photos)").fetchall()
        column_names = {column["name"] for column in columns}
        if "display_filename" not in column_names:
            connection.execute("ALTER TABLE photos ADD COLUMN display_filename TEXT")
        if "is_carousel" not in column_names:
            connection.execute("ALTER TABLE photos ADD COLUMN is_carousel INTEGER NOT NULL DEFAULT 0")

    if import_uploads:
        import_existing_uploads()
        ensure_display_images()


def import_existing_uploads():
    if not upload_path().exists():
        return

    with get_db_connection() as connection:
        for path in upload_path().iterdir():
            if not path.is_file() or not allowed_file(path.name):
                continue

            created_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
            display_filename = make_display_filename(path.name)
            connection.execute(
                """
                INSERT OR IGNORE INTO photos
                (filename, display_filename, title, description, location, shot_date, tags, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (path.name, display_filename, path.stem, "", "", "", "", created_at),
            )


def make_display_filename(filename):
    return f"{Path(filename).stem}.webp"


def create_display_image(source_filename):
    source = upload_path() / source_filename
    if not source.is_file():
        return None

    display_path().mkdir(parents=True, exist_ok=True)
    display_filename = make_display_filename(source_filename)
    target = display_path() / display_filename

    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return display_filename

    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((DISPLAY_IMAGE_MAX_WIDTH, DISPLAY_IMAGE_MAX_WIDTH), Image.Resampling.LANCZOS)

        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        image.save(target, "WEBP", quality=DISPLAY_IMAGE_QUALITY, method=6)

    return display_filename


def ensure_display_images():
    with get_db_connection() as connection:
        photos = connection.execute("SELECT id, filename, display_filename FROM photos").fetchall()
        for photo in photos:
            display_filename = photo["display_filename"] or make_display_filename(photo["filename"])
            target = display_path() / display_filename
            if not target.is_file():
                display_filename = create_display_image(photo["filename"])
            if display_filename and display_filename != photo["display_filename"]:
                connection.execute(
                    "UPDATE photos SET display_filename = ? WHERE id = ?",
                    (display_filename, photo["id"]),
                )


def get_photos():
    ensure_database()
    with get_db_connection() as connection:
        return connection.execute(
            "SELECT * FROM photos ORDER BY created_at DESC, id DESC"
        ).fetchall()


def get_photo(photo_id):
    ensure_database()
    with get_db_connection() as connection:
        return connection.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()


def get_carousel_photos():
    photos = get_photos()
    selected = [photo for photo in photos if photo["is_carousel"]]
    return selected if selected else photos[:2]


def get_selected_carousel_photos():
    ensure_database()
    with get_db_connection() as connection:
        return connection.execute(
            "SELECT * FROM photos WHERE is_carousel = 1 ORDER BY created_at DESC, id DESC"
        ).fetchall()


def get_collection_photos(photos, collection_index):
    if not photos:
        return []

    grouped_photos = photos[collection_index::len(COLLECTION_DEFINITIONS)]
    return grouped_photos if grouped_photos else photos


def get_collections(photos=None):
    photos = photos if photos is not None else get_photos()
    collections = []

    for index, collection in enumerate(COLLECTION_DEFINITIONS):
        collection_photos = get_collection_photos(photos, index)
        collections.append(
            {
                **collection,
                "cover": collection_photos[0] if collection_photos else None,
                "photos": collection_photos,
            }
        )

    return collections


def get_collection(slug):
    for collection in get_collections():
        if collection["slug"] == slug:
            return collection

    return None


def create_photo(filename, title, description, location, shot_date, tags):
    ensure_database(import_uploads=False)
    clean_title = title.strip() if title and title.strip() else "未命名照片"
    display_filename = create_display_image(filename)
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO photos
            (filename, display_filename, title, description, location, shot_date, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                display_filename,
                clean_title,
                description.strip(),
                location.strip(),
                shot_date.strip(),
                tags.strip(),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def update_carousel_status(photo_id, is_carousel):
    ensure_database()
    with get_db_connection() as connection:
        connection.execute(
            "UPDATE photos SET is_carousel = ? WHERE id = ?",
            (1 if is_carousel else 0, photo_id),
        )


def delete_photo_record(photo_id):
    photo = get_photo(photo_id)
    if photo is None:
        return False

    photo_file = upload_path() / photo["filename"]
    if photo_file.is_file():
        photo_file.unlink()

    display_filename = photo["display_filename"] or make_display_filename(photo["filename"])
    display_file = display_path() / display_filename
    if display_file.is_file():
        display_file.unlink()

    with get_db_connection() as connection:
        connection.execute("DELETE FROM photos WHERE id = ?", (photo_id,))

    return True


@app.route("/")
def index():
    return redirect(url_for("gallery"))


@app.route("/gallery")
def gallery():
    photos = get_photos()
    return render_template(
        "gallery.html",
        photos=photos,
        carousel_photos=get_carousel_photos(),
        collections=get_collections(photos),
    )


@app.route("/gallery/photo/<int:photo_id>")
def public_photo_detail(photo_id):
    photo = get_photo(photo_id)
    if photo is None or not (upload_path() / photo["filename"]).is_file():
        abort(404)

    return render_template("detail.html", photo=photo)


@app.route("/gallery/collections/<slug>")
def collection_detail(slug):
    collection = get_collection(slug)
    if collection is None:
        abort(404)

    return render_template("collection.html", collection=collection)


@app.route("/download/<int:photo_id>")
def download_photo(photo_id):
    photo = get_photo(photo_id)
    if photo is None or not (upload_path() / photo["filename"]).is_file():
        abort(404)

    return send_from_directory(app.config["UPLOAD_FOLDER"], photo["filename"], as_attachment=True)


@app.route("/admin")
def admin_dashboard():
    return render_template(
        "admin.html",
        photos=get_photos(),
        carousel_photos=get_selected_carousel_photos(),
    )


@app.route("/admin/photos/<int:photo_id>/carousel", methods=["POST"])
def admin_update_carousel(photo_id):
    photo = get_photo(photo_id)
    if photo is None:
        abort(404)

    is_carousel = request.form.get("is_carousel") == "1"
    update_carousel_status(photo_id, is_carousel)
    flash("轮播照片设置已更新。", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/photos/<int:photo_id>/delete", methods=["POST"])
def admin_delete_photo(photo_id):
    if not delete_photo_record(photo_id):
        abort(404)

    flash("照片已删除。", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/upload", methods=["GET", "POST"])
def admin_upload():
    if request.method == "POST":
        photo = request.files.get("photo")

        if photo is None or photo.filename == "":
            flash("请选择一张照片。", "error")
            return redirect(url_for("admin_upload"))

        if not allowed_file(photo.filename):
            flash("只支持 jpg、jpeg、png、webp 格式。", "error")
            return redirect(url_for("admin_upload"))

        original_name = secure_filename(photo.filename)
        suffix = Path(original_name).suffix.lower()
        saved_name = f"{uuid4().hex}{suffix}"
        upload_path().mkdir(exist_ok=True)
        photo.save(upload_path() / saved_name)
        create_photo(
            filename=saved_name,
            title=request.form.get("title", ""),
            description=request.form.get("description", ""),
            location=request.form.get("location", ""),
            shot_date=request.form.get("shot_date", ""),
            tags=request.form.get("tags", ""),
        )

        flash("照片和文字信息已保存。", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("upload.html")


@app.route("/upload")
def upload():
    return redirect(url_for("admin_upload"))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    if not allowed_file(filename) or not (upload_path() / filename).is_file():
        abort(404)

    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/display/<path:filename>")
def display_file(filename):
    if not filename.lower().endswith(".webp") or not (display_path() / filename).is_file():
        abort(404)

    return send_from_directory(display_path(), filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
