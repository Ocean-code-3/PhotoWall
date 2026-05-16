import os
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import abort, Flask, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename


app = Flask(__name__)
data_dir = Path(os.environ.get("DATA_DIR", app.root_path))
app.config["DATABASE"] = os.environ.get("DATABASE_PATH", str(data_dir / "photowall.db"))
app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", str(data_dir / "uploads"))
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY", "photo-wall-dev")

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def upload_path():
    return Path(app.config["UPLOAD_FOLDER"])


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
        if "is_carousel" not in column_names:
            connection.execute("ALTER TABLE photos ADD COLUMN is_carousel INTEGER NOT NULL DEFAULT 0")

    if import_uploads:
        import_existing_uploads()


def import_existing_uploads():
    if not upload_path().exists():
        return

    with get_db_connection() as connection:
        for path in upload_path().iterdir():
            if not path.is_file() or not allowed_file(path.name):
                continue

            created_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
            connection.execute(
                """
                INSERT OR IGNORE INTO photos
                (filename, title, description, location, shot_date, tags, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (path.name, path.stem, "", "", "", "", created_at),
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


def create_photo(filename, title, description, location, shot_date, tags):
    ensure_database(import_uploads=False)
    clean_title = title.strip() if title and title.strip() else "未命名照片"
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO photos
            (filename, title, description, location, shot_date, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
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

    with get_db_connection() as connection:
        connection.execute("DELETE FROM photos WHERE id = ?", (photo_id,))

    return True


@app.route("/")
def index():
    return redirect(url_for("gallery"))


@app.route("/gallery")
def gallery():
    return render_template("gallery.html", photos=get_photos(), carousel_photos=get_carousel_photos())


@app.route("/gallery/photo/<int:photo_id>")
def public_photo_detail(photo_id):
    photo = get_photo(photo_id)
    if photo is None or not (upload_path() / photo["filename"]).is_file():
        abort(404)

    return render_template("detail.html", photo=photo)


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
