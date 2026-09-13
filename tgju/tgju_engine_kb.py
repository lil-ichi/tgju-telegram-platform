# -*- coding: utf-8 -*-
"""Knowledge Base (KB) Engine for TGJU Platform.

Provides a unified knowledge base for AI agents and LLM automation tools:
- Directing AI to local folders and files
- Web URL fetching and scraping (URL Find)
- Direct guidelines, rules, and fundamental market notes
- Keyword / BM25 contextual retrieval for prompt injection into:
  * Poll generation & selection
  * Market analysis & posts
  * Future automation workflows and external agents
"""
import os
import re
import sys
import json
import time
import glob
import ipaddress
import socket
import uuid
import urllib.request
import urllib.parse
import html
from typing import Dict, List, Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
KB_DIR = os.path.join(STATE_DIR, "knowledge_base")
KB_CONFIG_PATH = os.path.join(STATE_DIR, "kb_config.json")
KB_UPLOAD_DIR = os.path.join(KB_DIR, "uploads")

DEFAULT_KB_CONFIG = {
    "enabled": True,
    "max_context_chars": 1800,
    "jobs": {
        "analysis": {"enabled": True, "max_chars": 1500},
        "poll_generate": {"enabled": True, "max_chars": 1200},
        "poll_select": {"enabled": True, "max_chars": 800},
        "news_summary": {"enabled": False, "max_chars": 1000},
    },
    "sources": {},  # id -> {id, title, type, path_or_url, enabled, word_count, char_count, last_synced, tags}
}

_CHUNK_SIZE = 1200
_CHUNK_OVERLAP = 200
_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10MB safety limit per file


def _public_http_url(raw_url: str) -> tuple:
    """Validate an HTTP(S) URL and reject local/private network targets."""
    value = (raw_url or "").strip()
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("آدرس اینترنتی معتبر نیست")
    if parsed.username or parsed.password:
        raise ValueError("URL نباید اطلاعات ورود داشته باشد")
    host = parsed.hostname.rstrip(".").lower()
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443,
                                                               type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError("میزبان URL قابل دسترسی نیست") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("دسترسی به شبکه داخلی یا آدرس خصوصی مجاز نیست")
    return value, host


def _ensure_dirs():
    os.makedirs(KB_DIR, exist_ok=True)
    os.makedirs(KB_UPLOAD_DIR, exist_ok=True)


def load_kb_config() -> dict:
    """Load configuration from state/kb_config.json merged with defaults."""
    _ensure_dirs()
    try:
        if os.path.exists(KB_CONFIG_PATH):
            with open(KB_CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
        else:
            saved = {}
    except Exception:
        saved = {}

    cfg = dict(DEFAULT_KB_CONFIG)
    cfg.update({k: v for k, v in saved.items() if v is not None})
    cfg.setdefault("sources", {})
    cfg.setdefault("jobs", dict(DEFAULT_KB_CONFIG["jobs"]))
    return cfg


def save_kb_config(cfg: dict):
    """Safely persist configuration to state/kb_config.json."""
    _ensure_dirs()
    tmp_path = KB_CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.replace(tmp_path, KB_CONFIG_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ── Text extraction & scraping helpers ──────────────────────────────────────

def _clean_html_text(raw_html: str) -> str:
    """Extract readable clean text from raw HTML without extra dependencies."""
    if not raw_html:
        return ""
    # Strip script and style blocks
    s = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', ' ', raw_html, flags=re.IGNORECASE)
    s = re.sub(r'<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>', ' ', s, flags=re.IGNORECASE)
    # Strip header/footer/nav tags to focus on main content if possible
    s = re.sub(r'<(?:nav|header|footer)\b[^<]*(?:(?!<\/(?:nav|header|footer)>)<[^<]*)*<\/(?:nav|header|footer)>', ' ', s, flags=re.IGNORECASE)
    # Convert breaks and paragraphs to newlines
    s = re.sub(r'<\s*br\s*\/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*\/p\s*>', '\n\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*\/div\s*>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*\/li\s*>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*h[1-6][^>]*>', '\n### ', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*\/h[1-6]\s*>', '\n', s, flags=re.IGNORECASE)
    # Remove remaining tags
    s = re.sub(r'<[^>]+>', ' ', s)
    # Unescape HTML entities
    s = html.unescape(s)
    # Clean redundant whitespace
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in s.splitlines()]
    # Remove excessive blank lines
    cleaned = []
    blank_count = 0
    for line in lines:
        if not line:
            blank_count += 1
            if blank_count <= 2:
                cleaned.append("")
        else:
            blank_count = 0
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def fetch_url_content(url: str, timeout: int = 15) -> Dict[str, Any]:
    """Fetch and parse an online URL or TGJU article into readable text."""
    try:
        url, _ = _public_http_url(url)
    except ValueError as exc:
        return {"ok": False, "url": url, "error": str(exc)}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        "Accept-Language": "fa,en-US;q=0.9,en;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "").lower()
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
            raw_bytes = resp.read(5 * 1024 * 1024)  # 5MB limit
            raw_text = raw_bytes.decode(charset, errors="replace")

        # Extract title
        title_m = re.search(r'<title[^>]*>(.*?)<\/title>', raw_text, flags=re.IGNORECASE | re.DOTALL)
        title = html.unescape(title_m.group(1).strip()) if title_m else url

        # Clean text
        text = _clean_html_text(raw_text)
        return {
            "ok": True,
            "url": url,
            "title": title,
            "text": text,
            "char_count": len(text),
            "word_count": len(text.split()),
        }
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)[:250]}


def read_file_content(path: str) -> Dict[str, Any]:
    """Read a local file (.txt, .md, .json, .csv, .pdf text extract)."""
    if not os.path.exists(path):
        return {"ok": False, "error": "فایل یافت نشد: %s" % path}
    if os.path.getsize(path) > _MAX_FILE_BYTES:
        return {"ok": False, "error": "حجم فایل بیش از سقف مجاز (۱۰ مگابایت) است"}

    ext = os.path.splitext(path)[1].lower()
    title = os.path.basename(path)

    try:
        if ext in (".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml"):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        elif ext in (".html", ".htm"):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            text = _clean_html_text(raw)
        elif ext == ".pdf":
            # Attempt to extract text from PDF if pypdf or pdfminer is available, or fallback
            text = _extract_pdf_text(path)
        else:
            # Generic text read
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()

        return {
            "ok": True,
            "path": path,
            "title": title,
            "text": text.strip(),
            "char_count": len(text),
            "word_count": len(text.split()),
        }
    except Exception as e:
        return {"ok": False, "path": path, "error": str(e)[:250]}


def _extract_pdf_text(path: str) -> str:
    """Best-effort PDF text extraction."""
    # 1. Try pypdf
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()
    except Exception:
        pass
    # 2. Try pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        return "\n\n".join(pages).strip()
    except Exception:
        pass
    # 3. Fallback: scan for readable text streams
    try:
        with open(path, "rb") as f:
            data = f.read()
        # Simple ascii/utf-8 string extractor
        strings = re.findall(b'[\x20-\x7E\xD8\x80-\xDF\xFF]{4,}', data)
        return " ".join([s.decode('utf-8', errors='ignore') for s in strings[:1000]])
    except Exception:
        return "(امکان استخراج متن از این فایل PDF فراهم نشد)"


def scan_directory(dir_path: str, recursive: bool = True) -> List[str]:
    """Return all readable document files in a directory."""
    if not os.path.isdir(dir_path):
        return []
    valid_exts = {".txt", ".md", ".json", ".csv", ".pdf", ".html", ".htm", ".log"}
    found = []
    if recursive:
        for root, _, files in os.walk(dir_path):
            for file in files:
                if os.path.splitext(file)[1].lower() in valid_exts and not file.startswith("."):
                    found.append(os.path.join(root, file))
    else:
        for file in os.listdir(dir_path):
            full = os.path.join(dir_path, file)
            if os.path.isfile(full) and os.path.splitext(file)[1].lower() in valid_exts and not file.startswith("."):
                found.append(full)
    return sorted(found)


# ── Chunking & Storage ─────────────────────────────────────────────────────

def _source_storage_path(source_id: str) -> str:
    _ensure_dirs()
    return os.path.join(KB_DIR, f"{source_id}.json")


def _chunk_text(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping semantic blocks."""
    if not text:
        return []
    paragraphs = text.split("\n\n")
    chunks = []
    current = []
    current_len = 0

    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        p_len = len(p)
        if current_len + p_len > chunk_size and current:
            chunks.append("\n\n".join(current))
            # retain last piece for overlap
            overlap_piece = current[-1] if len(current[-1]) < overlap else current[-1][-overlap:]
            current = [overlap_piece, p]
            current_len = len(overlap_piece) + p_len
        else:
            current.append(p)
            current_len += p_len

    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text[:chunk_size]]


def index_source_content(source_id: str, title: str, text: str, source_type: str, path_or_url: str, tags: list = None) -> dict:
    """Break source into chunks and store in state/knowledge_base/<id>.json."""
    _ensure_dirs()
    chunks = _chunk_text(text)
    doc = {
        "id": source_id,
        "title": title,
        "type": source_type,
        "path_or_url": path_or_url,
        "tags": tags or [],
        "char_count": len(text),
        "word_count": len(text.split()),
        "chunks": chunks,
        "raw_text": text,
        "indexed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(_source_storage_path(source_id), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return doc


def get_source_doc(source_id: str) -> Optional[dict]:
    """Load cached document for a source."""
    p = _source_storage_path(source_id)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── Management Operations ─────────────────────────────────────────────────

def add_folder_source(dir_path: str, title: str = "", tags: list = None) -> dict:
    """Direct AI to an entire folder as knowledge base."""
    dir_path = os.path.abspath(dir_path.strip().strip('"').strip("'"))
    if not os.path.isdir(dir_path):
        return {"ok": False, "error": f"مسیر پوشه وجود ندارد: {dir_path}"}

    files = scan_directory(dir_path)
    if not files:
        return {"ok": False, "error": "هیچ فایل متنی قابل‌خوانی در این پوشه یافت نشد (.txt, .md, .json, .csv, .pdf, .html)"}

    # Aggregate documents in folder
    combined_texts = []
    total_words = 0
    for fpath in files[:100]:  # limit to first 100 files for safety
        res = read_file_content(fpath)
        if res.get("ok") and res.get("text"):
            header = f"\n\n--- [سند: {os.path.basename(fpath)}] ---\n"
            combined_texts.append(header + res["text"])
            total_words += res["word_count"]

    full_text = "\n".join(combined_texts).strip()
    source_id = "fld_" + uuid.uuid4().hex[:12]
    src_title = title.strip() or f"پوشه {os.path.basename(dir_path)} ({len(files)} فایل)"

    index_source_content(source_id, src_title, full_text, "folder", dir_path, tags=tags)

    cfg = load_kb_config()
    cfg["sources"][source_id] = {
        "id": source_id,
        "title": src_title,
        "type": "folder",
        "path_or_url": dir_path,
        "enabled": True,
        "files_count": len(files),
        "word_count": total_words,
        "char_count": len(full_text),
        "last_synced": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tags": tags or ["folder", "local"],
    }
    save_kb_config(cfg)
    return {"ok": True, "source": cfg["sources"][source_id]}


def add_file_source(file_path: str, title: str = "", tags: list = None) -> dict:
    """Direct AI to a specific single file."""
    file_path = os.path.abspath(file_path.strip().strip('"').strip("'"))
    res = read_file_content(file_path)
    if not res.get("ok"):
        return res

    source_id = "fil_" + uuid.uuid4().hex[:12]
    src_title = title.strip() or res["title"]
    index_source_content(source_id, src_title, res["text"], "file", file_path, tags=tags)

    cfg = load_kb_config()
    cfg["sources"][source_id] = {
        "id": source_id,
        "title": src_title,
        "type": "file",
        "path_or_url": file_path,
        "enabled": True,
        "word_count": res["word_count"],
        "char_count": res["char_count"],
        "last_synced": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tags": tags or ["file", "local"],
    }
    save_kb_config(cfg)
    return {"ok": True, "source": cfg["sources"][source_id]}


def add_url_source(url: str, title: str = "", tags: list = None) -> dict:
    """Fetch and add a web URL or TGJU article to the knowledge base."""
    res = fetch_url_content(url)
    if not res.get("ok"):
        return res

    source_id = "url_" + uuid.uuid4().hex[:12]
    src_title = title.strip() or res["title"]
    index_source_content(source_id, src_title, res["text"], "url", res["url"], tags=tags)

    cfg = load_kb_config()
    cfg["sources"][source_id] = {
        "id": source_id,
        "title": src_title,
        "type": "url",
        "path_or_url": res["url"],
        "enabled": True,
        "word_count": res["word_count"],
        "char_count": res["char_count"],
        "last_synced": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tags": tags or ["web", "url"],
    }
    save_kb_config(cfg)
    return {"ok": True, "source": cfg["sources"][source_id]}


def add_snippet_source(title: str, text: str, tags: list = None) -> dict:
    """Add a direct manual snippet or rule."""
    title = title.strip() or "دستورالعمل محتوایی"
    text = text.strip()
    if not text:
        return {"ok": False, "error": "متن یادداشت نمی‌تواند خالی باشد"}

    source_id = "snp_" + uuid.uuid4().hex[:12]
    index_source_content(source_id, title, text, "snippet", "manual", tags=tags)

    cfg = load_kb_config()
    cfg["sources"][source_id] = {
        "id": source_id,
        "title": title,
        "type": "snippet",
        "path_or_url": "manual",
        "enabled": True,
        "word_count": len(text.split()),
        "char_count": len(text),
        "last_synced": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tags": tags or ["snippet", "manual"],
    }
    save_kb_config(cfg)
    return {"ok": True, "source": cfg["sources"][source_id]}


def save_uploaded_file(filename: str, content_bytes: bytes, title: str = "", tags: list = None) -> dict:
    """Save an uploaded file directly into state/knowledge_base/uploads/ and index it."""
    _ensure_dirs()
    if len(content_bytes) > _MAX_FILE_BYTES:
        return {"ok": False, "error": "حجم فایل بیش از سقف مجاز (۱۰ مگابایت) است"}
    safe_name = re.sub(r'[^a-zA-Z0-9_\u0600-\u06FF\.\-]', '_', filename)
    target_path = os.path.join(KB_UPLOAD_DIR, safe_name)
    # Avoid collision
    base, ext = os.path.splitext(target_path)
    counter = 1
    while os.path.exists(target_path):
        target_path = f"{base}_{counter}{ext}"
        counter += 1

    with open(target_path, "wb") as f:
        f.write(content_bytes)

    return add_file_source(target_path, title=title or filename, tags=tags or ["upload"])


def sync_source(source_id: str) -> dict:
    """Refresh content for a folder, file, or URL."""
    cfg = load_kb_config()
    src = (cfg.get("sources") or {}).get(source_id)
    if not src:
        return {"ok": False, "error": "منبع یافت نشد"}

    stype = src.get("type")
    path_or_url = src.get("path_or_url")
    title = src.get("title")
    tags = src.get("tags") or []

    if stype == "folder":
        refreshed = add_folder_source(path_or_url, title=title, tags=tags)
    elif stype == "file":
        refreshed = add_file_source(path_or_url, title=title, tags=tags)
    elif stype == "url":
        refreshed = add_url_source(path_or_url, title=title, tags=tags)
    else:
        return {"ok": True, "source": src, "note": "یادداشت متنی نیازی به همگام‌سازی ندارد"}
    if not refreshed.get("ok"):
        return refreshed

    # Preserve the configured source ID so UI references and toggles remain valid.
    new_id = refreshed["source"]["id"]
    new_doc = get_source_doc(new_id)
    if new_doc:
        new_doc["id"] = source_id
        with open(_source_storage_path(source_id), "w", encoding="utf-8") as f:
            json.dump(new_doc, f, ensure_ascii=False, indent=2)
    cfg = load_kb_config()
    cfg.get("sources", {}).pop(new_id, None)
    refreshed_meta = dict(refreshed["source"])
    refreshed_meta["id"] = source_id
    cfg.setdefault("sources", {})[source_id] = refreshed_meta
    save_kb_config(cfg)
    if new_id != source_id:
        try:
            os.remove(_source_storage_path(new_id))
        except OSError:
            pass
    return {"ok": True, "source": refreshed_meta}


def toggle_source(source_id: str, enabled: Optional[bool] = None) -> dict:
    """Enable or disable a knowledge source."""
    cfg = load_kb_config()
    src = (cfg.get("sources") or {}).get(source_id)
    if not src:
        return {"ok": False, "error": "منبع یافت نشد"}
    if enabled is None:
        src["enabled"] = not src.get("enabled", True)
    else:
        src["enabled"] = bool(enabled)
    save_kb_config(cfg)
    return {"ok": True, "source": src}


def delete_source(source_id: str) -> dict:
    """Remove a source from the knowledge base."""
    cfg = load_kb_config()
    if source_id in cfg.get("sources", {}):
        del cfg["sources"][source_id]
        save_kb_config(cfg)
    doc_path = _source_storage_path(source_id)
    if os.path.exists(doc_path):
        try:
            os.remove(doc_path)
        except Exception:
            pass
    return {"ok": True}


# ── Context Retrieval Engine (BM25 / Keyword Similarity) ───────────────────

_PERSIAN_STOPWORDS = {
    "از", "به", "با", "در", "بر", "تا", "برای", "که", "این", "آن", "یک", "را",
    "های", "شد", "است", "بود", "می", "هم", "نیز", "هر", "اگر", "اما", "یا",
    "and", "the", "in", "of", "to", "for", "on", "with", "as", "by", "at"
}


def _tokenize(text: str) -> List[str]:
    """Tokenize Persian and English text into words, removing punctuation."""
    words = re.findall(r'[\w\u0600-\u06FF]+', text.lower())
    return [w for w in words if len(w) > 1 and w not in _PERSIAN_STOPWORDS]


def search_kb(query: str, top_k: int = 4, tags: list = None) -> List[Dict[str, Any]]:
    """Search enabled sources for the most relevant chunks matching the query."""
    cfg = load_kb_config()
    if not cfg.get("enabled", True):
        return []

    sources = cfg.get("sources") or {}
    q_tokens = _tokenize(query)
    if not q_tokens:
        q_tokens = ["بازار", "قیمت", "طلا", "ارز"]

    results = []

    for sid, meta in sources.items():
        if not meta.get("enabled", True):
            continue
        if tags and not any(t in (meta.get("tags") or []) for t in tags):
            continue

        doc = get_source_doc(sid)
        if not doc:
            continue

        chunks = doc.get("chunks") or [doc.get("raw_text") or ""]
        title = doc.get("title") or meta.get("title")

        # Score chunks
        for idx, chunk in enumerate(chunks):
            if not chunk or len(chunk.strip()) < 20:
                continue
            c_tokens = _tokenize(chunk)
            if not c_tokens:
                continue

            score = 0.0
            # Title boost
            for t in q_tokens:
                if t in title.lower():
                    score += 3.0
                if t in chunk.lower():
                    # Term frequency
                    tf = chunk.lower().count(t)
                    score += min(5.0, 1.0 + 0.5 * tf)

            if score > 0:
                results.append({
                    "source_id": sid,
                    "title": title,
                    "type": meta.get("type"),
                    "chunk_index": idx,
                    "score": round(score, 2),
                    "text": chunk.strip(),
                })

    # Sort descending by score
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def get_kb_context(query: str = "", job: str = "", tags: list = None, max_chars: int = None) -> str:
    """Retrieve formatted Persian knowledge base context ready for LLM prompt injection."""
    cfg = load_kb_config()
    if not cfg.get("enabled", True):
        return ""

    if job:
        job_cfg = (cfg.get("jobs") or {}).get(job) or {}
        if not job_cfg.get("enabled", True):
            return ""
        if max_chars is None:
            max_chars = job_cfg.get("max_chars")

    if max_chars is None:
        max_chars = int(cfg.get("max_context_chars") or 1800)

    # Search top chunks
    matches = search_kb(query, top_k=4, tags=tags)
    if not matches:
        return ""

    context_parts = []
    current_len = 0

    for m in matches:
        part = f"📌 منبع [{m['title']}]:\n{m['text']}"
        if current_len + len(part) > max_chars:
            remaining = max_chars - current_len
            if remaining > 150:
                context_parts.append(part[:remaining] + "…")
            break
        context_parts.append(part)
        current_len += len(part)

    if not context_parts:
        return ""

    return "\n\n".join(context_parts)
