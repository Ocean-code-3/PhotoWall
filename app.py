import os
import hmac
import secrets
import sqlite3
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from flask import abort, Flask, flash, redirect, render_template, request, send_from_directory, session, url_for
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename


app = Flask(__name__)
data_dir = Path(os.environ.get("DATA_DIR", app.root_path))
app.config["DATABASE"] = os.environ.get("DATABASE_PATH", str(data_dir / "photowall.db"))
app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", str(data_dir / "uploads"))
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024


def read_or_create_private_value(filename, factory):
    private_dir = Path(app.instance_path)
    private_dir.mkdir(parents=True, exist_ok=True)
    target = private_dir / filename

    if target.is_file():
        value = target.read_text(encoding="utf-8").strip()
        if value:
            return value

    value = factory()
    target.write_text(f"{value}\n", encoding="utf-8")
    return value


app.secret_key = os.environ.get("SECRET_KEY") or read_or_create_private_value(
    "secret_key.txt",
    lambda: secrets.token_urlsafe(48),
)
app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD") or read_or_create_private_value(
    "admin_password.txt",
    lambda: secrets.token_urlsafe(18),
)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
DISPLAY_IMAGE_MAX_WIDTH = 1600
DISPLAY_IMAGE_QUALITY = 78
DISPLAY_IMAGE_METHOD = 4
CAROUSEL_IMAGE_MAX_WIDTH = 2800
CAROUSEL_IMAGE_QUALITY = 88
CAROUSEL_IMAGE_METHOD = 4
DEFAULT_COLLECTIONS = [
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


def carousel_path():
    return upload_path() / "carousel"


def database_path():
    return Path(app.config["DATABASE"])


def get_db_connection():
    connection = sqlite3.connect(database_path())
    connection.row_factory = sqlite3.Row
    return connection


def ensure_database(import_uploads=True):
    database_path().parent.mkdir(exist_ok=True)
    should_assign_existing_photos = False
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL UNIQUE,
                display_filename TEXT,
                collection_id INTEGER,
                title TEXT NOT NULL,
                description TEXT,
                location TEXT,
                shot_date TEXT,
                tags TEXT,
                created_at TEXT NOT NULL,
                is_carousel INTEGER NOT NULL DEFAULT 0,
                is_featured INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        columns = connection.execute("PRAGMA table_info(photos)").fetchall()
        column_names = {column["name"] for column in columns}
        if "display_filename" not in column_names:
            connection.execute("ALTER TABLE photos ADD COLUMN display_filename TEXT")
        if "collection_id" not in column_names:
            connection.execute("ALTER TABLE photos ADD COLUMN collection_id INTEGER")
            should_assign_existing_photos = True
        if "is_carousel" not in column_names:
            connection.execute("ALTER TABLE photos ADD COLUMN is_carousel INTEGER NOT NULL DEFAULT 0")
        if "is_featured" not in column_names:
            connection.execute("ALTER TABLE photos ADD COLUMN is_featured INTEGER NOT NULL DEFAULT 1")
        seed_default_collections(connection)

    if import_uploads:
        import_existing_uploads()
        ensure_display_images()
        ensure_carousel_images()

    if should_assign_existing_photos:
        assign_uncollected_photos()


def seed_default_collections(connection):
    collection_count = connection.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
    if collection_count:
        return

    created_at = datetime.now().isoformat(timespec="seconds")
    for collection in DEFAULT_COLLECTIONS:
        connection.execute(
            """
            INSERT INTO collections (slug, title, description, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (collection["slug"], collection["title"], collection["description"], created_at),
        )


def assign_uncollected_photos():
    with get_db_connection() as connection:
        collections = connection.execute("SELECT id FROM collections ORDER BY id").fetchall()
        if not collections:
            return

        photos = connection.execute(
            "SELECT id FROM photos WHERE collection_id IS NULL ORDER BY created_at DESC, id DESC"
        ).fetchall()
        collection_ids = [collection["id"] for collection in collections]
        for index, photo in enumerate(photos):
            connection.execute(
                "UPDATE photos SET collection_id = ? WHERE id = ?",
                (collection_ids[index % len(collection_ids)], photo["id"]),
            )


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
                (filename, display_filename, collection_id, title, description, location, shot_date, tags, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (path.name, display_filename, None, path.stem, "", "", "", "", created_at),
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

        image.save(target, "WEBP", quality=DISPLAY_IMAGE_QUALITY, method=DISPLAY_IMAGE_METHOD)

    return display_filename


def create_carousel_image(source_filename):
    source = upload_path() / source_filename
    if not source.is_file():
        return None

    carousel_path().mkdir(parents=True, exist_ok=True)
    carousel_filename = make_display_filename(source_filename)
    target = carousel_path() / carousel_filename

    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return carousel_filename

    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((CAROUSEL_IMAGE_MAX_WIDTH, CAROUSEL_IMAGE_MAX_WIDTH), Image.Resampling.LANCZOS)

        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        image.save(target, "WEBP", quality=CAROUSEL_IMAGE_QUALITY, method=CAROUSEL_IMAGE_METHOD)

    return carousel_filename


@lru_cache(maxsize=512)
def image_orientation(display_filename, modified_time):
    target = display_path() / display_filename
    if not target.is_file():
        return "landscape"

    with Image.open(target) as image:
        width, height = image.size

    if height > width * 1.12:
        return "portrait"
    if width > height * 1.12:
        return "landscape"
    return "square"


@app.template_global()
def photo_orientation(photo):
    display_filename = photo["display_filename"] or make_display_filename(photo["filename"])
    target = display_path() / display_filename
    modified_time = target.stat().st_mtime if target.is_file() else 0
    return image_orientation(display_filename, modified_time)


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


def ensure_carousel_images():
    with get_db_connection() as connection:
        photos = connection.execute("SELECT filename FROM photos").fetchall()
        for photo in photos:
            create_carousel_image(photo["filename"])


def get_photos():
    ensure_database()
    with get_db_connection() as connection:
        return connection.execute(
            """
            SELECT photos.*, collections.title AS collection_title, collections.slug AS collection_slug
            FROM photos
            LEFT JOIN collections ON collections.id = photos.collection_id
            ORDER BY photos.created_at DESC, photos.id DESC
            """
        ).fetchall()


def get_photo(photo_id):
    ensure_database()
    with get_db_connection() as connection:
        return connection.execute(
            """
            SELECT photos.*, collections.title AS collection_title, collections.slug AS collection_slug
            FROM photos
            LEFT JOIN collections ON collections.id = photos.collection_id
            WHERE photos.id = ?
            """,
            (photo_id,),
        ).fetchone()


def get_carousel_photos():
    photos = get_photos()
    return [photo for photo in photos if photo["is_carousel"]]


def get_featured_photos():
    ensure_database()
    with get_db_connection() as connection:
        return connection.execute(
            """
            SELECT photos.*, collections.title AS collection_title, collections.slug AS collection_slug
            FROM photos
            LEFT JOIN collections ON collections.id = photos.collection_id
            WHERE photos.is_featured = 1
            ORDER BY photos.created_at DESC, photos.id DESC
            """
        ).fetchall()


def arrange_mosaic_photos(photos):
    remaining = list(photos)
    arranged = []
    pattern = ["portrait", "landscape", "landscape", "portrait"]

    while remaining:
        for expected_orientation in pattern:
            if not remaining:
                break

            match_index = next(
                (
                    index
                    for index, photo in enumerate(remaining)
                    if photo_orientation(photo) == expected_orientation
                ),
                0,
            )
            arranged.append(remaining.pop(match_index))

    return arranged


@app.template_global()
def mosaic_layout_class(photos):
    photos = list(photos)
    portrait_count = sum(1 for photo in photos if photo_orientation(photo) == "portrait")
    mostly_portrait = photos and portrait_count >= 4 and portrait_count / len(photos) >= 0.75

    if photos and (portrait_count == len(photos) or mostly_portrait):
        return "is-portrait-grid"

    return "is-mixed-grid"


def get_selected_carousel_photos():
    ensure_database()
    with get_db_connection() as connection:
        return connection.execute(
            """
            SELECT photos.*, collections.title AS collection_title, collections.slug AS collection_slug
            FROM photos
            LEFT JOIN collections ON collections.id = photos.collection_id
            WHERE photos.is_carousel = 1
            ORDER BY photos.created_at DESC, photos.id DESC
            """
        ).fetchall()


def get_collection_photos(collection_id):
    with get_db_connection() as connection:
        return connection.execute(
            """
            SELECT photos.*, collections.title AS collection_title, collections.slug AS collection_slug
            FROM photos
            LEFT JOIN collections ON collections.id = photos.collection_id
            WHERE photos.collection_id = ?
            ORDER BY photos.created_at DESC, photos.id DESC
            """,
            (collection_id,),
        ).fetchall()


def get_collections(include_empty=False):
    ensure_database()
    with get_db_connection() as connection:
        collection_rows = connection.execute(
            "SELECT * FROM collections ORDER BY id ASC"
        ).fetchall()

    collections = []
    for row in collection_rows:
        photos = get_collection_photos(row["id"])
        if photos or include_empty:
            collections.append(
                {
                    "id": row["id"],
                    "slug": row["slug"],
                    "title": row["title"],
                    "description": row["description"],
                    "created_at": row["created_at"],
                    "cover": photos[0] if photos else None,
                    "photos": photos,
                }
            )

    return collections


def get_collection(slug):
    for collection in get_collections():
        if collection["slug"] == slug:
            return collection

    return None


def slugify_collection_title(title):
    base = secure_filename(title).lower().replace("_", "-")
    base = "-".join(part for part in base.split("-") if part)
    return base or f"collection-{uuid4().hex[:8]}"


def make_unique_collection_slug(title):
    base_slug = slugify_collection_title(title)
    slug = base_slug
    counter = 2

    with get_db_connection() as connection:
        while connection.execute("SELECT 1 FROM collections WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base_slug}-{counter}"
            counter += 1

    return slug


def create_collection(title, description):
    ensure_database(import_uploads=False)
    clean_title = title.strip() if title and title.strip() else "未命名作品集"
    slug = make_unique_collection_slug(clean_title)
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO collections (slug, title, description, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                slug,
                clean_title,
                description.strip(),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def update_collection(collection_id, title, description):
    ensure_database(import_uploads=False)
    clean_title = title.strip() if title and title.strip() else "未命名作品集"
    with get_db_connection() as connection:
        connection.execute(
            """
            UPDATE collections
            SET title = ?, description = ?
            WHERE id = ?
            """,
            (clean_title, description.strip(), collection_id),
        )


def get_collection_by_id(collection_id):
    ensure_database()
    with get_db_connection() as connection:
        collection = connection.execute(
            "SELECT * FROM collections WHERE id = ?",
            (collection_id,),
        ).fetchone()

    if collection is None:
        return None

    photos = get_collection_photos(collection["id"])
    return {
        "id": collection["id"],
        "slug": collection["slug"],
        "title": collection["title"],
        "description": collection["description"],
        "created_at": collection["created_at"],
        "cover": photos[0] if photos else None,
        "photos": photos,
    }


def create_photo(
    filename,
    title,
    description,
    location,
    shot_date,
    tags,
    collection_id=None,
    is_carousel=False,
    is_featured=False,
):
    ensure_database(import_uploads=False)
    clean_title = title.strip() if title and title.strip() else "未命名照片"
    display_filename = create_display_image(filename)
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO photos
            (filename, display_filename, collection_id, title, description, location, shot_date, tags, created_at, is_carousel, is_featured)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                display_filename,
                collection_id,
                clean_title,
                description.strip(),
                location.strip(),
                shot_date.strip(),
                tags.strip(),
                datetime.now().isoformat(timespec="seconds"),
                1 if is_carousel else 0,
                1 if is_featured else 0,
            ),
        )


def update_carousel_status(photo_id, is_carousel):
    ensure_database()
    with get_db_connection() as connection:
        connection.execute(
            "UPDATE photos SET is_carousel = ? WHERE id = ?",
            (1 if is_carousel else 0, photo_id),
        )


def update_featured_status(photo_id, is_featured):
    ensure_database()
    with get_db_connection() as connection:
        connection.execute(
            "UPDATE photos SET is_featured = ? WHERE id = ?",
            (1 if is_featured else 0, photo_id),
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


@app.before_request
def require_admin_login():
    if not request.path.startswith("/admin"):
        return None

    if request.endpoint in {"admin_login", "static"}:
        return None

    if session.get("admin_authenticated"):
        return None

    return redirect(url_for("admin_login", next=request.full_path.rstrip("?")))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(password, app.config["ADMIN_PASSWORD"]):
            session.clear()
            session["admin_authenticated"] = True
            flash("登录成功。", "success")
            next_url = request.form.get("next") or url_for("admin_dashboard")
            if not next_url.startswith("/admin"):
                next_url = url_for("admin_dashboard")
            return redirect(next_url)

        flash("密码不正确，请重试。", "error")

    return render_template("admin_login.html", next_url=request.args.get("next", ""))


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    flash("已退出管理后台。", "success")
    return redirect(url_for("admin_login"))


@app.route("/")
def index():
    return redirect(url_for("gallery"))


@app.route("/gallery")
def gallery():
    return render_template(
        "gallery.html",
        photos=arrange_mosaic_photos(get_featured_photos()),
        carousel_photos=get_carousel_photos(),
        collections=get_collections(),
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
        featured_photos=get_featured_photos(),
        collections=get_collections(include_empty=True),
    )


@app.route("/admin/export-static", methods=["POST"])
def admin_export_static():
    from export_static import build_static_site

    output_dir, page_count = build_static_site()
    flash(f"静态站已生成到 {output_dir}，共 {page_count} 个页面。", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/collections", methods=["POST"])
def admin_create_collection():
    create_collection(
        title=request.form.get("title", ""),
        description=request.form.get("description", ""),
    )
    flash("作品集已创建。", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/collections/<int:collection_id>/update", methods=["POST"])
def admin_update_collection(collection_id):
    collection = get_collection_by_id(collection_id)
    if collection is None:
        abort(404)

    update_collection(
        collection_id=collection_id,
        title=request.form.get("title", ""),
        description=request.form.get("description", ""),
    )
    flash("作品集信息已更新。", "success")
    return redirect(url_for("admin_collection_detail", collection_id=collection_id))


@app.route("/admin/collections/<int:collection_id>", methods=["GET", "POST"])
def admin_collection_detail(collection_id):
    collection = get_collection_by_id(collection_id)
    if collection is None:
        abort(404)

    if request.method == "POST":
        photo = request.files.get("photo")

        if photo is None or photo.filename == "":
            flash("请选择一张照片。", "error")
            return redirect(url_for("admin_collection_detail", collection_id=collection_id))

        if not allowed_file(photo.filename):
            flash("只支持 jpg、jpeg、png、webp 格式。", "error")
            return redirect(url_for("admin_collection_detail", collection_id=collection_id))

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
            collection_id=collection_id,
            is_carousel=request.form.get("is_carousel") == "1",
            is_featured=request.form.get("is_featured") == "1",
        )

        flash("照片已上传到作品集。", "success")
        return redirect(url_for("admin_collection_detail", collection_id=collection_id))

    return render_template("collection_admin.html", collection=collection)


@app.route("/admin/collections/<int:collection_id>/photos/<int:photo_id>/delete", methods=["POST"])
def admin_delete_collection_photo(collection_id, photo_id):
    collection = get_collection_by_id(collection_id)
    photo = get_photo(photo_id)
    if collection is None or photo is None or int(photo["collection_id"] or 0) != collection_id:
        abort(404)

    delete_photo_record(photo_id)
    flash("作品集照片已删除。", "success")
    return redirect(url_for("admin_collection_detail", collection_id=collection_id))


@app.route("/admin/photos/<int:photo_id>/carousel", methods=["POST"])
def admin_update_carousel(photo_id):
    photo = get_photo(photo_id)
    if photo is None:
        abort(404)

    is_carousel = request.form.get("is_carousel") == "1"
    update_carousel_status(photo_id, is_carousel)
    flash("轮播照片设置已更新。", "success")
    return redirect(request.form.get("next") or url_for("admin_dashboard"))


@app.route("/admin/photos/<int:photo_id>/featured", methods=["POST"])
def admin_update_featured(photo_id):
    photo = get_photo(photo_id)
    if photo is None:
        abort(404)

    is_featured = request.form.get("is_featured") == "1"
    update_featured_status(photo_id, is_featured)
    flash("错落照片流设置已更新。", "success")
    return redirect(request.form.get("next") or url_for("admin_dashboard"))


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

        collection_id = request.form.get("collection_id")
        if not collection_id or get_collection_by_id(collection_id) is None:
            flash("请先选择照片所属的作品集。", "error")
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
            collection_id=collection_id,
            is_carousel=request.form.get("is_carousel") == "1",
            is_featured=request.form.get("is_featured") == "1",
        )

        flash("照片和文字信息已保存。", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("upload.html", collections=get_collections(include_empty=True))


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


@app.route("/carousel/<path:filename>")
def carousel_file(filename):
    if not filename.lower().endswith(".webp") or not (carousel_path() / filename).is_file():
        abort(404)

    return send_from_directory(carousel_path(), filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
