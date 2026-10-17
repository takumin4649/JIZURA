#!/usr/bin/env python3
"""Find commercially usable audio candidates in Openverse for JIZURA/short videos.

Search never downloads. Downloading requires an explicit second command.
Only CC0 and CC BY are accepted by default; metadata is NOT legal clearance.
Standard library only; no API key required for anonymous requests.
"""
import argparse
import ipaddress
import json
import mimetypes
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.openverse.org/v1/audio/"
ALLOWED_LICENSES = frozenset(("cc0", "by"))
MAX_AUDIO_BYTES = 25 * 1024 * 1024
USER_AGENT = "JIZURA-Openverse-Audio/1.0 (audio asset lookup)"
MEDIA_EXTENSIONS = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
    "audio/ogg": ".ogg", "audio/flac": ".flac", "audio/x-flac": ".flac",
    "audio/mp4": ".m4a", "audio/webm": ".webm", "audio/aac": ".aac",
}


def https_public_url(value):
    """Disallow local URLs, credentials, and non-HTTPS sources."""
    if not isinstance(value, str):
        return False
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.hostname.lower() == "localhost" or parsed.hostname.endswith(".localhost"):
        return False
    try:
        if not ipaddress.ip_address(parsed.hostname).is_global:
            return False
    except ValueError:
        pass
    return True


def search_audio(query, category="sound_effect", limit=5, timeout=15):
    if not (1 <= len(query.strip()) <= 200):
        raise ValueError("query must be 1–200 characters")
    if category not in ("sound_effect", "music"):
        raise ValueError("category must be sound_effect or music")
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    params = urllib.parse.urlencode({
        "q": query.strip(),
        "category": category,
        "license": ",".join(sorted(ALLOWED_LICENSES)),
        "license_type": "commercial",
        "mature": "false",
        "page_size": limit,
    })
    request = urllib.request.Request(
        API_URL + "?" + params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("unexpected Openverse API response")
    items = []
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        license_name = str(item.get("license", "")).lower()
        if license_name not in ALLOWED_LICENSES or not https_public_url(item.get("url")):
            continue
        if item.get("mature"):
            continue
        items.append({
            "id": str(item.get("id", "")),
            "title": item.get("title") or "(untitled)",
            "creator": item.get("creator") or "(unknown)",
            "category": item.get("category"),
            "duration": item.get("duration"),
            "license": license_name,
            "license_version": item.get("license_version"),
            "license_url": item.get("license_url"),
            "attribution": item.get("attribution"),
            "source_page": item.get("foreign_landing_url"),
            "audio_url": item["url"],
            "source": item.get("source"),
        })
    return {
        "provider": "Openverse",
        "searched_at_utc": datetime.now(timezone.utc).isoformat(),
        "query": query.strip(),
        "category": category,
        "licenses_allowed": sorted(ALLOWED_LICENSES),
        "items": items,
        "warning": "Search metadata is unverified. Check the original source and license before publishing; CC BY requires attribution.",
    }


def fetch_audio(item, directory, timeout=30):
    if item.get("license") not in ALLOWED_LICENSES:
        raise ValueError("License is not on the commercial-use allowlist")
    url = item.get("audio_url")
    if not https_public_url(url):
        raise ValueError("Audio URL must be public HTTPS")
    ident = re.sub(r"[^a-zA-Z0-9_-]", "", str(item.get("id", "")))[:80]
    if not ident:
        raise ValueError("Missing or invalid audio ID")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not https_public_url(response.geturl()):
            raise ValueError("Audio redirect must stay on public HTTPS")
        mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if mime and not (mime.startswith("audio/") or mime == "application/octet-stream"):
            raise ValueError("Non-audio server response: " + mime)
        extension = MEDIA_EXTENSIONS.get(mime)
        if not extension:
            path_ext = Path(urllib.parse.urlsplit(response.geturl()).path).suffix.lower()
            extension = path_ext if path_ext in MEDIA_EXTENSIONS.values() else ".audio"
        destination = directory / (ident + extension)
        info_path = directory / (ident + ".json")
        credit_path = directory / (ident + ".attribution.txt")
        if any(p.exists() for p in (destination, info_path, credit_path)):
            raise FileExistsError("Output exists; choose an empty directory")
        chunks, size = [], 0
        while True:
            block = response.read(128 * 1024)
            if not block:
                break
            size += len(block)
            if size > MAX_AUDIO_BYTES:
                raise ValueError("Audio exceeds 25 MiB limit")
            chunks.append(block)
    if size == 0:
        raise ValueError("Empty audio response")
    destination.write_bytes(b"".join(chunks))
    metadata = dict(item, saved_file=str(destination), bytes=size,
                    downloaded_at_utc=datetime.now(timezone.utc).isoformat())
    info_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    credit = (
        f"Title: {item.get('title') or '(untitled)'}\n"
        f"Creator: {item.get('creator') or '(unknown)'}\n"
        f"License: {item.get('license', '').upper()} {item.get('license_version') or ''}\n"
        f"License URL: {item.get('license_url') or '(not provided)'}\n"
        f"Source page: {item.get('source_page') or '(not provided)'}\n"
        f"Openverse attribution: {item.get('attribution') or '(not provided)'}\n\n"
        "IMPORTANT: Verify the original source, license, and attribution requirements before publishing.\n"
    )
    credit_path.write_text(credit, encoding="utf-8")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Search candidates and save review manifest (no download)")
    search.add_argument("--query", required=True, help="English search phrase usually gives more results")
    search.add_argument("--category", choices=("sound_effect", "music"), default="sound_effect")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--manifest", type=Path, default=Path("openverse_audio_candidates.json"))
    download = commands.add_parser("download", help="Download one reviewed candidate and keep credit metadata")
    download.add_argument("--manifest", type=Path, required=True)
    download.add_argument("--number", type=int, required=True, help="1-based result number")
    download.add_argument("--out-dir", type=Path, default=Path("audio_assets"))
    args = parser.parse_args(argv)
    try:
        if args.command == "search":
            result = search_audio(args.query, args.category, args.limit)
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print(f"Manifest: {args.manifest}", file=sys.stderr)
        else:
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            items = manifest.get("items", [])
            if not isinstance(items, list) or not 1 <= args.number <= len(items):
                raise ValueError("Invalid candidate number")
            result = fetch_audio(items[args.number - 1], args.out_dir)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, FileExistsError, OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        print(f"Openverse audio: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
