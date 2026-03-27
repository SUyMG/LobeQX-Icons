from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PACKAGE_DIR = ROOT / ".tmp" / "upstream" / "package"
CONFIG_PATH = ROOT / "scripts" / "aliases.json"
OUTPUT_ROOT = ROOT / "IconSet"
CANVAS_SIZE = 144
SAFE_PADDING = 1
ALPHA_CROP_THRESHOLD = 8
ALPHA_NOISE_THRESHOLD = 6
LARGE_SOURCE_SIZE = 512
THEMES = ("light", "dark")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate QX-ready icons from @lobehub/icons-static-png.")
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=DEFAULT_PACKAGE_DIR,
        help="Path to the unpacked npm package directory.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limit the number of files generated per theme for local testing. 0 means no limit.",
    )
    parser.add_argument(
        "--skip-aliases",
        action="store_true",
        help="Skip generating alias icons.",
    )
    return parser.parse_args()


def load_config() -> tuple[dict[str, str], list[str]]:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    aliases = {normalize_relative_path(key): value for key, value in raw.get("aliases", {}).items()}
    exclude = [normalize_relative_path(item) for item in raw.get("exclude", [])]
    return aliases, exclude


def normalize_relative_path(value: str) -> str:
    return value.replace("\\", "/").lstrip("./")


def encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_if_changed(path: Path, data: bytes) -> bool:
    if path.exists() and sha256(path.read_bytes()) == sha256(data):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def should_exclude(relative_path: str, exclude_rules: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative_path, rule) for rule in exclude_rules)


def contain(image: Image.Image, box_size: int) -> Image.Image:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("image has invalid dimensions")
    scale = min(box_size / width, box_size / height)
    resized = (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )
    return image.resize(resized, Image.Resampling.LANCZOS)


def clean_large_source_alpha(image: Image.Image) -> Image.Image:
    if max(image.size) < LARGE_SOURCE_SIZE:
        return image

    alpha = image.getchannel("A").point(lambda a: 0 if a <= ALPHA_NOISE_THRESHOLD else a)
    cleaned = image.copy()
    cleaned.putalpha(alpha)
    return cleaned


def trim_transparent_bounds(image: Image.Image) -> Image.Image:
    alpha_mask = image.getchannel("A").point(lambda a: 255 if a > ALPHA_CROP_THRESHOLD else 0)
    alpha_bbox = alpha_mask.getbbox()
    if alpha_bbox is None:
        return image
    return image.crop(alpha_bbox)


def prepare_image(source_path: Path) -> Image.Image:
    image = Image.open(source_path).convert("RGBA")
    cleaned = clean_large_source_alpha(image)
    trimmed = trim_transparent_bounds(cleaned)
    content = contain(trimmed, CANVAS_SIZE - SAFE_PADDING * 2)
    canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    x = (CANVAS_SIZE - content.width) // 2
    y = (CANVAS_SIZE - content.height) // 2
    canvas.alpha_composite(content, (x, y))
    return canvas


def sync_removed_files(directory: Path, expected_files: set[str], prune: bool = True) -> int:
    removed = 0
    if (not prune) or (not directory.exists()):
        return removed

    for png_file in directory.glob("*.png"):
        if png_file.name not in expected_files:
            png_file.unlink()
            removed += 1
    return removed


def iter_theme_files(package_dir: Path, theme: str, max_files: int) -> list[Path]:
    theme_dir = package_dir / theme
    if not theme_dir.is_dir():
        raise FileNotFoundError(f"missing theme directory: {theme_dir}")

    files = sorted(theme_dir.glob("*.png"))
    if max_files > 0:
        return files[:max_files]
    return files


def generate_theme_outputs(package_dir: Path, theme: str, exclude_rules: list[str], max_files: int, prune: bool) -> int:
    changed = 0
    generated_names: set[str] = set()
    output_dir = OUTPUT_ROOT / theme

    for source_path in iter_theme_files(package_dir, theme, max_files):
        relative = normalize_relative_path(source_path.relative_to(package_dir).as_posix())
        if should_exclude(relative, exclude_rules):
            print(f"excluded: {relative}")
            continue

        image = prepare_image(source_path)
        output_name = source_path.name
        output_path = output_dir / output_name
        updated = write_if_changed(output_path, encode_png(image))
        generated_names.add(output_name)
        status = "updated" if updated else "unchanged"
        if updated:
            changed += 1
        print(f"{status}: {theme}/{output_name}")

    removed = sync_removed_files(output_dir, generated_names, prune=prune)
    if removed:
        changed += removed
        print(f"removed: {removed} stale file(s) from {theme}/")

    return changed


def generate_alias_outputs(package_dir: Path, aliases: dict[str, str]) -> int:
    changed = 0
    output_dir = OUTPUT_ROOT / "aliases"
    generated_names: set[str] = set()

    for source_relative, output_name in sorted(aliases.items()):
        source_path = package_dir / source_relative
        if not source_path.is_file():
            print(f"skipped alias: {output_name} (missing source {source_relative})", file=sys.stderr)
            continue

        image = prepare_image(source_path)
        output_path = output_dir / output_name
        updated = write_if_changed(output_path, encode_png(image))
        generated_names.add(output_name)
        status = "updated" if updated else "unchanged"
        if updated:
            changed += 1
        print(f"{status}: aliases/{output_name}")

    removed = sync_removed_files(output_dir, generated_names)
    if removed:
        changed += removed
        print(f"removed: {removed} stale file(s) from aliases/")

    return changed


def main() -> int:
    args = parse_args()
    package_dir = args.package_dir.resolve()
    if not package_dir.is_dir():
        print(f"error: package directory not found: {package_dir}", file=sys.stderr)
        return 1

    aliases, exclude_rules = load_config()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    changed = 0
    prune_outputs = args.max_files == 0
    for theme in THEMES:
        changed += generate_theme_outputs(package_dir, theme, exclude_rules, args.max_files, prune_outputs)

    if args.skip_aliases:
        print("aliases: skipped by flag")
    else:
        changed += generate_alias_outputs(package_dir, aliases)

    print(f"done: {changed} file(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())











