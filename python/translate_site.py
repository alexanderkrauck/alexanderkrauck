#!/usr/bin/env python3
"""Static site translator with DeepL support.

The script clones a directory of HTML files into ``<root>/<target-lang>/`` while
translating textual content through the DeepL API and injecting a language switcher
overlay in both source and translated pages.

Example::

    python translate_site.py hosting de --source-lang en

Requirements:
    pip install beautifulsoup4 requests python-dotenv

DeepL authentication:
    export DEEPL_API_KEY=...  # or pass --deepl-api-key
    (values from a project .env file are loaded automatically)

Optional overrides:
    Create a JSON file mapping original strings to desired translations and
    pass it with --translation-overrides.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Dict, Iterator, List, Sequence, Tuple

from bs4 import BeautifulSoup, Comment, Doctype  # type: ignore
import requests  # type: ignore
from dotenv import load_dotenv  # type: ignore

DEEPL_API_FREE_URL = "https://api-free.deepl.com/v2/translate"
DEEPL_API_PRO_URL = "https://api.deepl.com/v2/translate"

HTML_EXTENSIONS = {".html", ".htm"}
ATTRIBUTE_CANDIDATES = (
    "title",
    "alt",
    "placeholder",
    "aria-label",
    "aria-placeholder",
    "aria-valuetext",
    "aria-description",
)
SKIP_PARENTS = {"script", "style", "code", "pre"}
OVERLAY_CONTAINER_ID = "language-switcher"
OVERLAY_STYLE_ID = "language-switcher-styles"
LANG_SWITCHER_STYLES = """
#language-switcher {
    position: fixed;
    bottom: 24px;
    right: 24px;
    display: flex;
    gap: 8px;
    padding: 8px 12px;
    background: rgba(0, 0, 0, 0.75);
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.2);
    backdrop-filter: blur(8px);
    z-index: 2147483647;
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
#language-switcher a {
    color: #f8fafc;
    text-decoration: none;
    font-size: 0.85rem;
    padding: 6px 12px;
    border-radius: 999px;
    border: 1px solid transparent;
    transition: background 0.2s ease, color 0.2s ease, border-color 0.2s ease;
}
#language-switcher a:hover {
    background: rgba(255, 255, 255, 0.12);
}
#language-switcher a.active {
    background: rgba(255, 255, 255, 0.9);
    color: #0f172a;
    border-color: rgba(15, 23, 42, 0.2);
    cursor: default;
}
@media (max-width: 640px) {
    #language-switcher {
        bottom: 16px;
        right: 50%;
        transform: translateX(50%);
    }
}
"""

load_dotenv()

_WHITESPACE_LEADING = re.compile(r"^\s+")
_WHITESPACE_TRAILING = re.compile(r"\s+$")


@dataclass
class LanguageEntry:
    code: str
    label: str
    href: str
    active: bool


class DeeplTranslator:
    def __init__(self, api_key: str, base_url: str | None = None, *, batch_size: int = 25) -> None:
        self.api_key = api_key
        self.base_url = base_url or DEEPL_API_FREE_URL
        self.batch_size = batch_size
        self._cache: Dict[str, str] = {}

    def translate_many(self, texts: Sequence[str], source_lang: str, target_lang: str) -> List[str]:
        missing = [t for t in texts if t not in self._cache]
        for chunk in _chunked(missing, self.batch_size):
            if not chunk:
                continue
            results = self._translate_batch(chunk, source_lang, target_lang)
            for original, translated in zip(chunk, results):
                self._cache[original] = translated
        return [self._cache.get(t, t) for t in texts]

    def _translate_batch(self, chunk: Sequence[str], source_lang: str, target_lang: str) -> List[str]:
        form: List[Tuple[str, str]] = [
            ("auth_key", self.api_key),
            ("source_lang", source_lang.upper()),
            ("target_lang", target_lang.upper()),
            ("preserve_formatting", "1"),
        ]
        form.extend(("text", text) for text in chunk)
        response = self._post(form)
        if response.status_code == 456:
            raise RuntimeError("DeepL quota exceeded (HTTP 456).")
        if response.status_code >= 400:
            raise RuntimeError(f"DeepL request failed ({response.status_code}): {response.text}")
        data = response.json()
        translations = data.get("translations", [])
        if len(translations) != len(chunk):
            raise RuntimeError("Mismatch between request texts and DeepL response.")
        return [item.get("text", "") for item in translations]

    def _post(self, form: List[Tuple[str, str]]) -> requests.Response:
        response = requests.post(self.base_url, data=form, timeout=30)
        if response.status_code == 403 and self.base_url == DEEPL_API_FREE_URL:
            # Likely using a pro key; retry against the pro endpoint once.
            self.base_url = DEEPL_API_PRO_URL
            response = requests.post(self.base_url, data=form, timeout=30)
        return response


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = pathlib.Path(args.site_root).resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Directory not found: {root}")

    api_key = args.deepl_api_key or os.getenv("DEEPL_API_KEY")
    if not api_key:
        raise SystemExit("DeepL API key missing. Use --deepl-api-key or set DEEPL_API_KEY.")

    target_lang = args.target_lang.lower()
    source_lang = args.source_lang.lower()

    target_root = root / target_lang
    if target_root.exists():
        if not args.force:
            raise SystemExit(f"Translation target already exists: {target_root}. Use --force to overwrite.")
        shutil.rmtree(target_root)

    overrides: Dict[str, str] = {}
    if args.translation_overrides:
        override_path = pathlib.Path(args.translation_overrides).resolve()
        if not override_path.exists():
            raise SystemExit(f"Override file not found: {override_path}")
        overrides = _load_overrides(override_path)
        print(f"Loaded {len(overrides)} translation override(s) from {override_path}")

    existing_lang_dirs = _detect_language_dirs(root)
    skip_lang_dirs = set(existing_lang_dirs)
    skip_lang_dirs.add(target_lang)

    translator = DeeplTranslator(api_key)

    processed_pages = 0
    copied_assets = 0
    translated_segments = 0

    for rel_path in _iter_site_files(root, skip_lang_dirs):
        src_path = root / rel_path
        dest_path = target_root / rel_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        if src_path.suffix.lower() in HTML_EXTENSIONS:
            processed_pages += 1
            segments = translate_html(
                src_path=src_path,
                dest_path=dest_path,
                translator=translator,
                source_lang=source_lang,
                target_lang=target_lang,
                overrides=overrides,
            )
            translated_segments += segments
            print(f"[translate] {rel_path} -> {target_lang}/{rel_path} ({segments} segment(s))")
        else:
            copied_assets += 1
            shutil.copy2(src_path, dest_path)

    if not args.skip_overlay:
        discovered_langs = existing_lang_dirs | _detect_language_dirs(root) | {target_lang}
        languages = [source_lang]
        for lang in sorted(discovered_langs - {source_lang}):
            languages.append(lang)
        language_roots: Dict[str, pathlib.Path] = {source_lang: root}
        for lang in languages:
            if lang == source_lang:
                continue
            lang_path = root / lang
            if lang_path.exists():
                language_roots[lang] = lang_path
        refresh_language_overlays(
            root=root,
            languages=languages,
            source_lang=source_lang,
            language_roots=language_roots,
            update_source=not args.skip_source_overlay,
        )

    print(
        f"Translated {processed_pages} HTML file(s) ({translated_segments} total segment(s)). "
        f"Copied {copied_assets} asset(s) to {target_root}."
    )
    return 0


def translate_html(
    *,
    src_path: pathlib.Path,
    dest_path: pathlib.Path,
    translator: DeeplTranslator,
    source_lang: str,
    target_lang: str,
    overrides: Dict[str, str],
) -> int:
    original_text = src_path.read_text(encoding="utf-8")

    soup = BeautifulSoup(original_text, "html.parser")

    segment_count = apply_translation(soup, translator, source_lang, target_lang, overrides)
    if soup.html and soup.html.has_attr("lang"):
        soup.html["lang"] = target_lang
    elif soup.html:
        soup.html.attrs["lang"] = target_lang

    translated_html = str(soup)

    dest_path.write_text(translated_html, encoding="utf-8")
    return segment_count


def ensure_overlay(
    *,
    html: str,
    entries: Sequence[LanguageEntry],
    replace_existing: bool,
) -> str:
    soup = BeautifulSoup(html, "html.parser")

    existing = soup.find(id=OVERLAY_CONTAINER_ID)
    if existing and not replace_existing:
        return html  # Overlay already present, assume links are configured manually.
    if existing and replace_existing:
        existing.decompose()

    if not soup.head:
        soup.insert(0, soup.new_tag("head"))
    if not soup.body:
        soup.append(soup.new_tag("body"))

    if not soup.find("style", id=OVERLAY_STYLE_ID):
        style_tag = soup.new_tag("style", id=OVERLAY_STYLE_ID)
        style_tag.string = LANG_SWITCHER_STYLES
        soup.head.append(style_tag)

    container = soup.new_tag("div", id=OVERLAY_CONTAINER_ID)
    container["class"] = ["language-switcher"]
    container["data-no-translate"] = "true"

    for entry in entries:
        container.append(
            _build_lang_link(
                soup,
                label=entry.label,
                href=entry.href,
                active=entry.active,
            )
        )

    soup.body.insert(0, container)
    return str(soup)


def _build_lang_link(soup: BeautifulSoup, *, label: str, href: str, active: bool) -> BeautifulSoup:
    tag = soup.new_tag("a")
    tag.string = label
    tag["href"] = href
    tag["class"] = ["active"] if active else []
    if active:
        tag["aria-current"] = "true"
    return tag


def apply_translation(
    soup: BeautifulSoup,
    translator: DeeplTranslator,
    source_lang: str,
    target_lang: str,
    overrides: Dict[str, str],
) -> int:
    text_nodes: List[Tuple[BeautifulSoup, str]] = []
    texts: List[str] = []

    for node in soup.find_all(string=True):
        if isinstance(node, (Comment, Doctype)):
            continue
        parent = node.parent
        if parent and parent.name and parent.name.lower() in SKIP_PARENTS:
            continue
        if node.find_parent(attrs={"data-no-translate": True}):
            continue
        text = str(node)
        if text.strip():
            text_nodes.append((node, text))
            texts.append(text)

    attr_targets: List[Tuple[BeautifulSoup, str, str]] = []
    for element in soup.find_all(True):
        for attr in ATTRIBUTE_CANDIDATES:
            value = element.attrs.get(attr)
            if not isinstance(value, str) or not value.strip():
                continue
            if element.has_attr("data-no-translate") or element.find_parent(attrs={"data-no-translate": True}):
                continue
            attr_targets.append((element, attr, value))
            texts.append(value)

    translations = translator.translate_many(texts, source_lang, target_lang)

    text_idx = 0
    for node, original in text_nodes:
        translated = _maybe_override(original, translations[text_idx], overrides)
        text_idx += 1
        node.replace_with(_rewrap_whitespace(original, translated))

    for element, attr, original in attr_targets:
        translated = _maybe_override(original, translations[text_idx], overrides)
        text_idx += 1
        element[attr] = _rewrap_whitespace(original, translated)

    return len(texts)


def _rewrap_whitespace(original: str, translated: str) -> str:
    if not original.strip():
        return original
    leading_match = _WHITESPACE_LEADING.match(original)
    trailing_match = _WHITESPACE_TRAILING.search(original)
    leading = leading_match.group(0) if leading_match else ""
    trailing = trailing_match.group(0) if trailing_match else ""
    return f"{leading}{translated.strip()}{trailing}"


def _relative_href(current_path: pathlib.Path, target_path: pathlib.Path) -> str:
    current_dir = current_path.parent
    rel = os.path.relpath(target_path, current_dir)
    rel = rel.replace(os.path.sep, "/")
    return rel


def _language_entries_for(
    *,
    rel_path: pathlib.Path,
    current_lang: str,
    languages: Sequence[str],
    language_roots: Dict[str, pathlib.Path],
    current_path: pathlib.Path,
) -> List[LanguageEntry]:
    entries: List[LanguageEntry] = []
    for lang in languages:
        lang_root = language_roots.get(lang)
        if lang_root is None:
            continue
        variant_path = lang_root / rel_path
        if not variant_path.exists():
            continue
        href = _relative_href(current_path, variant_path)
        entries.append(
            LanguageEntry(
                code=lang,
                label=lang.upper(),
                href=href,
                active=(lang == current_lang),
            )
        )
    return entries


def _iter_site_files(root: pathlib.Path, skip_langs: Sequence[str]) -> Iterator[pathlib.Path]:
    skip_set = {lang.lower() for lang in skip_langs}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0].lower() in skip_set:
            continue  # Skip existing target directory so we don't nest endlessly.
        yield relative


def _chunked(seq: Sequence[str], size: int) -> Iterator[List[str]]:
    for idx in range(0, len(seq), size):
        yield list(seq[idx : idx + size])


def _maybe_override(original: str, translated: str, overrides: Dict[str, str]) -> str:
    if not overrides:
        return translated
    key = original.strip()
    return overrides.get(key, translated)


def _load_overrides(path: pathlib.Path) -> Dict[str, str]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SystemExit("Override file must contain a JSON object of {source: target}.")
    cleaned: Dict[str, str] = {}
    for src, tgt in data.items():
        if not isinstance(src, str) or not isinstance(tgt, str):
            raise SystemExit("Override file entries must be strings.")
        cleaned[src.strip()] = tgt
    return cleaned


def _detect_language_dirs(root: pathlib.Path) -> set[str]:
    lang_pattern = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,3})?$", re.IGNORECASE)
    langs: set[str] = set()
    for child in root.iterdir():
        if not child.is_dir() or not lang_pattern.match(child.name):
            continue
        has_html = next(child.rglob("*.html"), None)
        if not has_html:
            continue
        langs.add(child.name.lower())
    return langs


def refresh_language_overlays(
    *,
    root: pathlib.Path,
    languages: Sequence[str],
    source_lang: str,
    language_roots: Dict[str, pathlib.Path],
    update_source: bool,
) -> None:
    other_langs_lower = {lang.lower() for lang in languages if lang != source_lang}
    for lang in languages:
        if lang == source_lang and not update_source:
            continue
        lang_root = language_roots.get(lang)
        if not lang_root or not lang_root.exists():
            continue
        base_root = root if lang == source_lang else lang_root
        for path in base_root.rglob("*.html"):
            try:
                rel_path = path.relative_to(base_root)
            except ValueError:
                continue
            if lang == source_lang and rel_path.parts and rel_path.parts[0].lower() in other_langs_lower:
                continue
            entries = _language_entries_for(
                rel_path=rel_path,
                current_lang=lang,
                languages=languages,
                language_roots=language_roots,
                current_path=path,
            )
            original_html = path.read_text(encoding="utf-8")
            updated_html = ensure_overlay(
                html=original_html,
                entries=entries,
                replace_existing=True,
            )
            if updated_html != original_html:
                path.write_text(updated_html, encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate a directory of HTML files via DeepL.")
    parser.add_argument("site_root", help="Root directory of the static site (e.g. hosting)")
    parser.add_argument("target_lang", help="Target language code (e.g. de, fr, es)")
    parser.add_argument("--source-lang", default="en", help="Source language code (default: en)")
    parser.add_argument("--deepl-api-key", dest="deepl_api_key", help="DeepL auth key (overrides env)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing <root>/<target>/ directory if present")
    parser.add_argument("--skip-overlay", action="store_true", help="Do not inject language switcher overlay into translated pages")
    parser.add_argument("--skip-source-overlay", action="store_true", help="Do not modify source files to add overlay")
    parser.add_argument(
        "--translation-overrides",
        help="Path to JSON file with {original: desired translation} entries",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
