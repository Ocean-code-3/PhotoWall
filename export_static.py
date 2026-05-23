import os
import re
import shutil
from pathlib import Path

from app import (
    app,
    carousel_path,
    display_path,
    ensure_database,
    get_collections,
    get_photos,
    upload_path,
)


DEFAULT_BASE_PATH = "/PhotoWall"
DEFAULT_OUTPUT_DIR = "docs"


def clean_output_dir(output_dir):
    output_dir = output_dir.resolve()
    project_dir = Path(app.root_path).resolve()

    if output_dir == project_dir or project_dir not in output_dir.parents:
        raise ValueError(f"Refusing to clean unsafe output directory: {output_dir}")

    if output_dir.exists():
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True)


def copy_directory(source, target):
    if source.exists():
        shutil.copytree(source, target, dirs_exist_ok=True)


def copy_static_assets(output_dir):
    copy_directory(Path(app.root_path) / "static", output_dir / "static")
    copy_directory(display_path(), output_dir / "display")
    copy_directory(carousel_path(), output_dir / "carousel")

    uploads_target = output_dir / "uploads"
    uploads_target.mkdir(parents=True, exist_ok=True)
    for photo in get_photos():
        source = upload_path() / photo["filename"]
        if source.is_file():
            shutil.copy2(source, uploads_target / photo["filename"])


def prefix_root_urls(html, base_path):
    base_path = base_path.strip()
    if not base_path or base_path == "/":
        return html

    base_path = "/" + base_path.strip("/")
    return re.sub(r'(?P<attr>\b(?:href|src|action)=["\'])/(?!/)', rf"\g<attr>{base_path}/", html)


def render_route(client, route, output_file, base_path):
    response = client.get(route)
    if response.status_code not in {200, 302}:
        raise RuntimeError(f"Failed to render {route}: HTTP {response.status_code}")

    if response.status_code == 302:
        response = client.get(response.headers["Location"])
        if response.status_code != 200:
            raise RuntimeError(f"Failed to follow redirect for {route}: HTTP {response.status_code}")

    html = response.get_data(as_text=True)
    html = prefix_root_urls(html, base_path)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(html, encoding="utf-8")


def build_static_site(output_dir=DEFAULT_OUTPUT_DIR, base_path=DEFAULT_BASE_PATH):
    output_dir = Path(output_dir)
    clean_output_dir(output_dir)

    ensure_database()
    copy_static_assets(output_dir)

    routes = [
        ("/gallery", output_dir / "index.html"),
        ("/gallery", output_dir / "gallery" / "index.html"),
    ]

    for photo in get_photos():
        routes.append((f"/gallery/photo/{photo['id']}", output_dir / "gallery" / "photo" / str(photo["id"]) / "index.html"))

    for collection in get_collections():
        routes.append(
            (
                f"/gallery/collections/{collection['slug']}",
                output_dir / "gallery" / "collections" / collection["slug"] / "index.html",
            )
        )

    with app.test_client() as client:
        for route, output_file in routes:
            render_route(client, route, output_file, base_path)

    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    return output_dir.resolve(), len(routes)


if __name__ == "__main__":
    target_dir, page_count = build_static_site(
        output_dir=os.environ.get("STATIC_EXPORT_DIR", DEFAULT_OUTPUT_DIR),
        base_path=os.environ.get("STATIC_BASE_PATH", DEFAULT_BASE_PATH),
    )
    print(f"Exported {page_count} pages to {target_dir}")
