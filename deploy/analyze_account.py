#!/usr/bin/env python3
"""
analyze_account.py — анализ Instagram аккаунта по 5 критериям.

Оптимизации:
  - 2 вызова Xpoz (профиль + 20 постов) вместо 4–6
  - Критерии 1–3 и 5 считаются на Python (нет LLM-токенов)
  - Критерий 4 (монетизация) — Claude Haiku + prompt cache
  - Xpoz вызовы напрямую через HTTP, без MCP tool schemas в контексте

Usage:
    python analyze_account.py whop
    python analyze_account.py cristiano --save
    python analyze_account.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import threading

import httpx


def _bootstrap_local_env_from_file() -> None:
    """Подхватить ключи из xpoz/ops_console.local.env при запуске из CLI без uvicorn."""
    path = Path(__file__).resolve().parent.parent / "ops_console.local.env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _load_xpoz_tokens() -> list[str]:
    raw = os.environ.get("XPOZ_API_KEYS", "").strip()
    if raw:
        return [t.strip() for t in raw.split(",") if t.strip()]
    single = os.environ.get("XPOZ_API_KEY", "").strip()
    if single:
        return [single]
    return []


_bootstrap_local_env_from_file()

# ── Конфиг ──────────────────────────────────────────────────────────────────
XPOZ_TOKENS = _load_xpoz_tokens()
XPOZ_URL     = "https://mcp.xpoz.ai/mcp"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Thread-safe key rotation
_key_lock      = threading.Lock()
_key_idx       = 0
_key_cooldown  = {}   # idx → time when usable again


def _get_token() -> str:
    with _key_lock:
        if not XPOZ_TOKENS:
            return ""
        return XPOZ_TOKENS[_key_idx % len(XPOZ_TOKENS)]


def _rotate_token(failed_idx: int | None = None):
    """Rotate to next key after quota/429. Thread-safe."""
    global _key_idx
    with _key_lock:
        if not XPOZ_TOKENS:
            return
        if failed_idx is not None and failed_idx != _key_idx % len(XPOZ_TOKENS):
            return  # already rotated by another thread
        _key_idx += 1
        new_idx = _key_idx % len(XPOZ_TOKENS)
        print(f"  ⟳ Rotating to Xpoz key {new_idx + 1}/{len(XPOZ_TOKENS)}", flush=True)

POSTS_LIMIT  = 20   # нужно для bottom-10 из 15 + запас
DAYS_90      = 90
DAYS_30      = 30

# ── Модели ──────────────────────────────────────────────────────────────────
MODEL_HAIKU  = "claude-haiku-4-5-20251001"

# ── Системный промпт для критерия 4 (кешируется) ────────────────────────────
MONETIZATION_SYSTEM = """You are an Instagram content analyst.
Your task: determine if an Instagram account shows monetization signals and classify the business.

Monetization signals = posts or bio CTAs promoting:
- Paid products: courses, coaching, consultations, mastermind, program, bootcamp
- Free lead magnets: free course, free guide, free webinar, free community, free challenge
- Communities (paid OR free): Discord, Telegram, membership, subscribe to join
- Webinars and workshops
- Digital products, templates, subscriptions, newsletters, software, ecommerce offers

NOT signals:
- "Link in bio" -> YouTube videos (free content only)
- Brand deals promoting other companies/products
- Paid posts promoting other bloggers
- Platform/marketplace CTAs (e.g. "sell on whop.com")

Return strict JSON only with these keys:
{
  "has_signals": true,
  "signals_found": ["specific CTA or monetization phrases"],
  "reasoning": "1-2 sentences",
  "offer_type": "course|coaching|consulting|agency_service|community_membership|webinar_workshop|newsletter|digital_product|affiliate|ecommerce|unknown",
  "funnel_type": "book_call|dm_to_buy|link_in_bio|lead_magnet|webinar_funnel|waitlist|subscribe_join|direct_checkout|unknown",
  "business_model": "education|service_business|community_business|audience_monetization|software_tool|commerce_brand|media_only|unknown",
  "audience_type": "b2b|consumer|creator_economy|local_business|mixed|unknown",
  "monetization_strength": "none|weak|moderate|strong",
  "cta_keywords": ["cta keywords only"],
  "bio_keywords": ["niche keywords only"],
  "confidence": 0.0,
  "icp": "ICP1|ICP2|ICP3|ICP4|ICP5|unknown"
}"""

OFFER_TYPES = {
    "course",
    "coaching",
    "consulting",
    "agency_service",
    "community_membership",
    "webinar_workshop",
    "newsletter",
    "digital_product",
    "affiliate",
    "ecommerce",
    "unknown",
}
FUNNEL_TYPES = {
    "book_call",
    "dm_to_buy",
    "link_in_bio",
    "lead_magnet",
    "webinar_funnel",
    "waitlist",
    "subscribe_join",
    "direct_checkout",
    "unknown",
}
BUSINESS_MODELS = {
    "education",
    "service_business",
    "community_business",
    "audience_monetization",
    "software_tool",
    "commerce_brand",
    "media_only",
    "unknown",
}
AUDIENCE_TYPES = {"b2b", "consumer", "creator_economy", "local_business", "mixed", "unknown"}
MONETIZATION_STRENGTHS = {"none", "weak", "moderate", "strong"}
PLATFORM_MIXES = {
    "instagram_only",
    "instagram_youtube",
    "instagram_twitter",
    "instagram_telegram",
    "instagram_website",
    "multi_channel",
}
STOPWORDS = {
    "with", "that", "this", "your", "from", "into", "about", "there", "their", "have",
    "will", "want", "help", "them", "they", "instagram", "creator", "founder", "coach",
    "agency", "community", "program", "course", "bio", "link", "join", "free", "best",
}
KEYWORD_RULES = {
    "course": ("course", "program", "bootcamp", "masterclass", "academy"),
    "coaching": ("coach", "coaching", "mentor", "mentorship", "1:1", "mastermind"),
    "consulting": ("consulting", "consultant", "strategy", "audit"),
    "agency_service": ("agency", "done for you", "dfy", "service", "client"),
    "community_membership": ("community", "membership", "discord", "telegram", "subscribe to join"),
    "webinar_workshop": ("webinar", "workshop", "training", "live session"),
    "newsletter": ("newsletter", "substack", "weekly email"),
    "digital_product": ("template", "toolkit", "ebook", "digital product", "download"),
    "affiliate": ("affiliate", "commission", "promo code", "discount code"),
    "ecommerce": ("shop", "store", "merch", "etsy", "product"),
}


# ── Xpoz HTTP клиент ─────────────────────────────────────────────────────────

def xpoz_call(tool: str, args: dict, max_poll: int = 10) -> dict:
    """Вызов Xpoz MCP tool напрямую через HTTP. Handles polling для async ops."""
    if not XPOZ_TOKENS:
        return {
            "error": "Xpoz API key missing: set XPOZ_API_KEY or XPOZ_API_KEYS (comma-separated)",
        }
    retries = len(XPOZ_TOKENS) + 1
    for attempt in range(retries):
        token     = _get_token()
        key_idx   = _key_idx % len(XPOZ_TOKENS)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }

        try:
            with httpx.Client(timeout=90) as client:
                resp = client.post(XPOZ_URL, json=payload, headers=headers)

            if resp.status_code == 429 or "quota" in resp.text.lower() or "USAGE_LIMIT_EXCEEDED" in resp.text:
                print(f"  ⚠ Xpoz quota/429 on key {key_idx + 1}, rotating...", flush=True)
                _rotate_token(key_idx)
                time.sleep(30)
                continue

            resp.raise_for_status()
        except httpx.TimeoutException:
            time.sleep(3)
            continue
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                _rotate_token(key_idx)
                time.sleep(30)
                continue
            raise

        data = _parse_sse(resp.text)

        # Async operation → poll until done
        op_id = data.get("operationId")
        if op_id and data.get("status") == "running":
            for _ in range(max_poll):
                time.sleep(8)
                poll_payload = {
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "checkOperationStatus",
                               "arguments": {"operationId": op_id}},
                }
                try:
                    with httpx.Client(timeout=90) as client:
                        resp = client.post(XPOZ_URL, json=poll_payload, headers=headers)
                except httpx.TimeoutException:
                    continue
                data = _parse_sse(resp.text)
                if data.get("success") or "results" in str(data):
                    break
                if data.get("status") not in ("running", None):
                    break

        return data

    return {"error": "All Xpoz keys exhausted or quota exceeded"}


def _parse_sse(text: str) -> dict:
    """Извлечь JSON из SSE stream."""
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                outer = json.loads(line[5:].strip())
                # MCP wraps in result.content[].text (YAML-ish string)
                content = (outer.get("result") or {}).get("content") or []
                if content and content[0].get("type") == "text":
                    return _parse_yaml_ish(content[0]["text"])
                # Error
                if "error" in outer:
                    return {"error": outer["error"].get("message", str(outer["error"]))}
            except json.JSONDecodeError:
                pass
    return {}


def _parse_yaml_ish(text: str) -> dict:
    """Минимальный парсер для YAML-like ответов Xpoz."""
    if text.strip().startswith("{"):
        try:
            return json.loads(text)
        except Exception:
            pass

    result: dict = {}
    lines = text.splitlines()

    # Detect CSV table rows: results[N]{fields}: row1, row2, ...
    in_results = False
    rows = []
    fields_order = []

    for line in lines:
        stripped = line.strip()

        # Top-level key: value
        if ":" in stripped and not stripped.startswith("-") and not in_results:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            # results[N]{field1,field2,...}: — table header
            if key.startswith("results[") and "{" in key:
                in_results = True
                fields_part = key[key.index("{")+1:key.index("}")]
                fields_order = [f.strip() for f in fields_part.split(",")]
                result["results"] = []
                continue

            if key in ("success",):
                result[key] = val.lower() == "true"
            elif val.isdigit():
                result[key] = int(val)
            elif val.startswith('"') and val.endswith('"'):
                result[key] = val[1:-1]
            else:
                result[key] = val

        elif in_results and stripped:
            # Each line is: id,val1,val2,...,caption (CSV with possible quoted fields)
            try:
                row_values = _csv_split(stripped)
                if len(row_values) >= len(fields_order):
                    row = {}
                    for i, f in enumerate(fields_order):
                        row[f] = row_values[i] if i < len(row_values) else None
                    rows.append(row)
            except Exception:
                pass

    if in_results:
        result["results"] = rows

    return result


def _csv_split(line: str) -> list:
    """Разбить CSV строку с учётом кавычек."""
    import csv
    reader = csv.reader([line])
    return list(next(reader, []))


# ── Вспомогательные функции ──────────────────────────────────────────────────

def safe_int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse_date(s) -> Optional[datetime]:
    if not s or s in ("null", "0", "None"):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(str(s)[:19].rstrip("Z"), fmt[:len(str(s)[:10])+9])
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _clean_list(raw: object, *, limit: int = 8) -> list[str]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        items = re.split(r"[,;\n]", raw)
    else:
        items = []
    seen = set()
    cleaned: list[str] = []
    for item in items:
        text = str(item or "").strip().strip('"').strip("'")
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text[:80])
        if len(cleaned) >= limit:
            break
    return cleaned


def _pick_enum(raw: object, allowed: set[str], *, default: str = "unknown") -> str:
    text = str(raw or "").strip().lower()
    return text if text in allowed else default


def _extract_keywords(text: str, *, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+&.-]{3,}", text.lower())
    seen = set()
    keywords: list[str] = []
    for token in tokens:
        if token in STOPWORDS or token.isdigit():
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def _extract_primary_domain(external_url: str, other_socials: dict[str, object]) -> str:
    urls: list[str] = []
    if external_url:
        urls.append(external_url)
    for value in other_socials.values():
        if isinstance(value, dict):
            maybe_url = str(value.get("url") or "").strip()
            if maybe_url:
                urls.append(maybe_url)
        elif isinstance(value, str):
            urls.append(value)
    for raw_url in urls:
        candidate = raw_url.strip()
        if not candidate or "." not in candidate:
            continue
        try:
            parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
        except Exception:
            continue
        host = (parsed.netloc or parsed.path).lower().strip()
        if host.startswith("www."):
            host = host[4:]
        if not host:
            continue
        if any(platform in host for platform in ("instagram.com", "youtube.com", "youtu.be", "twitter.com", "x.com", "tiktok.com", "linkedin.com", "t.me", "telegram.me")):
            continue
        return host
    return ""


def _infer_platform_mix(other_socials: dict[str, object], external_url: str) -> str:
    has_youtube = bool(other_socials.get("youtube"))
    has_twitter = bool(other_socials.get("twitter") or other_socials.get("twitter_bio"))
    has_telegram = bool(other_socials.get("telegram"))
    has_site = bool(_extract_primary_domain(external_url, other_socials))
    count = sum([has_youtube, has_twitter, has_telegram, has_site])
    if count >= 2:
        return "multi_channel"
    if has_youtube:
        return "instagram_youtube"
    if has_twitter:
        return "instagram_twitter"
    if has_telegram:
        return "instagram_telegram"
    if has_site:
        return "instagram_website"
    return "instagram_only"


def _infer_language(text: str) -> str:
    sample = (text or "").lower()
    if not sample.strip():
        return "unknown"
    if re.search(r"[а-яё]", sample):
        return "ru"
    if any(word in sample for word in ("hola", "espa", "latam", "mexico", "madrid", "barcelona")):
        return "es"
    if any(word in sample for word in ("ola", "brasil", "portugal", "lisboa", "sao paulo")):
        return "pt"
    return "en"


def _infer_geo_hint(text: str, external_url: str) -> str:
    lowered = f"{text} {external_url}".lower()
    geo_markers = {
        "usa": ("usa", "united states", "new york", "miami", "los angeles", "california"),
        "uk": ("london", "uk", "united kingdom", "england"),
        "uae": ("dubai", "abu dhabi", "uae", "emirates"),
        "canada": ("canada", "toronto", "vancouver"),
        "australia": ("australia", "sydney", "melbourne"),
        "germany": ("germany", "berlin", ".de"),
        "france": ("france", "paris", ".fr"),
        "spain": ("spain", "madrid", "barcelona", ".es"),
        "brazil": ("brazil", "brasil", "sao paulo", ".br"),
        "russia": ("moscow", "russia", "россия", ".ru"),
    }
    for geo, markers in geo_markers.items():
        if any(marker in lowered for marker in markers):
            return geo
    return "unknown"


def _infer_business_model(offer_type: str, signals: list[str]) -> str:
    lowered = " ".join(signals).lower()
    if offer_type in {"course", "coaching", "webinar_workshop"}:
        return "education"
    if offer_type in {"consulting", "agency_service"}:
        return "service_business"
    if offer_type in {"community_membership", "newsletter"}:
        return "community_business"
    if offer_type == "digital_product":
        return "audience_monetization"
    if "software" in lowered or "saas" in lowered or "app" in lowered:
        return "software_tool"
    if offer_type in {"ecommerce", "affiliate"}:
        return "commerce_brand"
    return "media_only" if signals else "unknown"


def _infer_audience_type(text: str) -> str:
    lowered = text.lower()
    b2b_markers = ("founder", "agency", "brand", "business", "b2b", "ceo", "consultant", "freelancer")
    consumer_markers = ("mom", "fitness", "beauty", "recipe", "fashion", "lifestyle", "travel", "wellness")
    has_b2b = any(marker in lowered for marker in b2b_markers)
    has_consumer = any(marker in lowered for marker in consumer_markers)
    if has_b2b and has_consumer:
        return "mixed"
    if has_b2b:
        return "b2b"
    if has_consumer:
        return "consumer"
    if "creator" in lowered:
        return "creator_economy"
    if any(marker in lowered for marker in ("local", "studio", "clinic", "salon", "restaurant")):
        return "local_business"
    return "unknown"


def _infer_icp(offer_type: str, business_model: str, audience_type: str) -> str:
    if offer_type in {"course", "coaching", "webinar_workshop"}:
        return "ICP1"
    if offer_type in {"consulting", "agency_service"} or audience_type == "b2b":
        return "ICP2"
    if offer_type in {"community_membership", "newsletter"}:
        return "ICP3"
    if business_model in {"commerce_brand", "software_tool"} or offer_type in {"ecommerce", "affiliate"}:
        return "ICP4"
    if offer_type in {"digital_product", "unknown"}:
        return "ICP5"
    return "unknown"


def _heuristic_taxonomy(text: str) -> dict:
    lowered = text.lower()
    signals: list[str] = []
    offer_type = "unknown"
    for candidate, patterns in KEYWORD_RULES.items():
        if any(pattern in lowered for pattern in patterns):
            offer_type = candidate
            signals.extend(pattern for pattern in patterns if pattern in lowered)
            break
    funnel_type = "unknown"
    if any(pattern in lowered for pattern in ("book a call", "book call", "strategy call", "apply now", "application")):
        funnel_type = "book_call"
    elif any(pattern in lowered for pattern in ("dm me", "message me", "send dm", "reply dm")):
        funnel_type = "dm_to_buy"
    elif any(pattern in lowered for pattern in ("free guide", "free checklist", "free training", "lead magnet", "freebie")):
        funnel_type = "lead_magnet"
    elif any(pattern in lowered for pattern in ("webinar", "workshop", "masterclass")):
        funnel_type = "webinar_funnel"
    elif any(pattern in lowered for pattern in ("waitlist", "join waitlist")):
        funnel_type = "waitlist"
    elif any(pattern in lowered for pattern in ("join", "subscribe", "membership", "community")):
        funnel_type = "subscribe_join"
    elif any(pattern in lowered for pattern in ("buy now", "shop now", "checkout")):
        funnel_type = "direct_checkout"
    elif "link in bio" in lowered:
        funnel_type = "link_in_bio"

    signals = _clean_list(signals or _extract_cta_keywords(text))
    business_model = _infer_business_model(offer_type, signals)
    audience_type = _infer_audience_type(text)
    strength = "strong" if len(signals) >= 3 else "moderate" if len(signals) >= 2 else "weak" if signals else "none"
    icp = _infer_icp(offer_type, business_model, audience_type)
    return {
        "has_signals": bool(signals),
        "signals_found": signals,
        "reasoning": "Heuristic fallback based on bio/caption keywords.",
        "offer_type": offer_type,
        "funnel_type": funnel_type,
        "business_model": business_model,
        "audience_type": audience_type,
        "monetization_strength": strength,
        "cta_keywords": _extract_cta_keywords(text),
        "bio_keywords": _extract_keywords(text),
        "confidence": 0.45 if signals else 0.2,
        "icp": icp,
    }


def _extract_cta_keywords(text: str, *, limit: int = 8) -> list[str]:
    candidates = [
        "book a call",
        "dm me",
        "link in bio",
        "free guide",
        "free training",
        "free webinar",
        "join community",
        "join newsletter",
        "shop now",
        "apply now",
        "consulting",
        "coaching",
        "course",
        "masterclass",
        "template",
    ]
    lowered = text.lower()
    found = [candidate for candidate in candidates if candidate in lowered]
    return _clean_list(found, limit=limit)


def normalize_taxonomy_result(
    raw_result: dict,
    *,
    biography: str,
    external_url: str,
    other_socials: dict[str, object],
    captions_text: str,
) -> dict:
    fallback = _heuristic_taxonomy(f"{biography}\n{captions_text}\n{external_url}")
    merged = dict(fallback)
    if isinstance(raw_result, dict):
        merged.update({k: v for k, v in raw_result.items() if v not in (None, "")})

    signals = _clean_list(merged.get("signals_found") or fallback["signals_found"])
    cta_keywords = _clean_list(merged.get("cta_keywords") or _extract_cta_keywords(f"{biography}\n{captions_text}"))
    bio_keywords = _clean_list(merged.get("bio_keywords") or _extract_keywords(biography))
    offer_type = _pick_enum(merged.get("offer_type"), OFFER_TYPES)
    funnel_type = _pick_enum(merged.get("funnel_type"), FUNNEL_TYPES)
    business_model = _pick_enum(
        merged.get("business_model"),
        BUSINESS_MODELS,
        default=_infer_business_model(offer_type, signals),
    )
    audience_type = _pick_enum(
        merged.get("audience_type"),
        AUDIENCE_TYPES,
        default=_infer_audience_type(f"{biography}\n{captions_text}"),
    )
    strength = _pick_enum(
        merged.get("monetization_strength"),
        MONETIZATION_STRENGTHS,
        default=fallback["monetization_strength"],
    )
    confidence = merged.get("confidence", fallback["confidence"])
    try:
        confidence_value = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_value = float(fallback["confidence"])
    primary_domain = _extract_primary_domain(external_url, other_socials)
    platform_mix = _infer_platform_mix(other_socials, external_url)
    language = _infer_language(biography)
    geo_hint = _infer_geo_hint(biography, external_url)
    icp = str(merged.get("icp") or "").strip().upper()
    if icp not in {"ICP1", "ICP2", "ICP3", "ICP4", "ICP5"}:
        icp = _infer_icp(offer_type, business_model, audience_type)

    return {
        "has_signals": bool(merged.get("has_signals", bool(signals))),
        "signals_found": signals,
        "reasoning": str(merged.get("reasoning") or fallback["reasoning"]).strip(),
        "offer_type": offer_type,
        "funnel_type": funnel_type,
        "business_model": business_model,
        "audience_type": audience_type,
        "monetization_strength": strength,
        "cta_keywords": cta_keywords,
        "bio_keywords": bio_keywords,
        "confidence": round(confidence_value, 3),
        "platform_mix": platform_mix if platform_mix in PLATFORM_MIXES else "instagram_only",
        "primary_domain": primary_domain,
        "language": language,
        "geo_hint": geo_hint,
        "icp": icp,
    }


# ── Получение данных ──────────────────────────────────────────────────────────

def _search_instagram_id(username: str) -> Optional[str]:
    """Real-time поиск ID аккаунта через searchInstagramUsers (~5 кредитов)."""
    data = xpoz_call("searchInstagramUsers", {
        "name": username,
        "limit": 5,
    })
    rows = data.get("results") or data.get("data") or []
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict):
            uname = (row.get("username") or "").lower()
            if uname == username.lower():
                return str(row.get("id", ""))
    return None


def fetch_profile(username: str) -> dict:
    """Профиль: followerCount, biography, externalUrl.
    Сначала ищет в базе Xpoz, при fol=0 делает real-time fallback через searchInstagramUsers.
    """
    fields = ["id", "username", "fullName", "biography",
              "followerCount", "followingCount", "mediaCount",
              "isVerified", "isPrivate", "externalUrl"]

    data = xpoz_call("getInstagramUser", {
        "identifier": username,
        "identifierType": "username",
        "fields": fields,
    })
    nested = data.get("data")
    if isinstance(nested, dict) and nested.get("username"):
        profile = nested
    else:
        profile = data

    # Если followerCount пустой — пробуем real-time через ID
    fol = safe_int(profile.get("followerCount"))
    if fol == 0 and not profile.get("error"):
        # Сначала пробуем ID из самого профиля
        existing_id = str(profile.get("id") or "").strip()
        if not existing_id:
            existing_id = _search_instagram_id(username) or ""

        if existing_id:
            rt_data = xpoz_call("getInstagramUser", {
                "identifier": existing_id,
                "identifierType": "id",
                "fields": fields,
            })
            rt_nested = rt_data.get("data")
            if isinstance(rt_nested, dict) and rt_nested.get("username"):
                rt_profile = rt_nested
            else:
                rt_profile = rt_data

            if safe_int(rt_profile.get("followerCount")) > 0:
                rt_profile["_realtime"] = True
                rt_profile["_ig_id"] = existing_id
                return rt_profile

    return profile


def fetch_posts(username: str, ig_id: Optional[str] = None) -> list[dict]:
    """Последние POSTS_LIMIT постов с метриками.
    Если передан ig_id — использует его (real-time режим через ID).
    """
    fields = ["id", "postType", "mediaType", "caption",
              "createdAtDate", "likeCount", "commentCount",
              "reshareCount", "videoPlayCount"]

    if ig_id:
        data = xpoz_call("getInstagramPostsByUser", {
            "identifier": ig_id,
            "identifierType": "id",
            "limit": POSTS_LIMIT,
            "fields": fields,
        })
    else:
        data = xpoz_call("getInstagramPostsByUser", {
            "identifier": username,
            "identifierType": "username",
            "limit": POSTS_LIMIT,
            "fields": fields,
        })
    return data.get("results", [])


def fetch_twitter(username: str) -> Optional[dict]:
    """Twitter профиль (best-effort, не критично)."""
    data = xpoz_call("getTwitterUser", {
        "identifier": username,
        "identifierType": "username",
        "fields": ["id", "username", "name", "description",
                   "followersCount", "tweetCount", "isVerified"],
    })
    if data.get("error") or not data.get("success", True):
        return None
    return data.get("data", data)


def is_reel(post: dict) -> bool:
    media = str(post.get("mediaType") or "").lower()
    ptype = str(post.get("postType") or "").lower()
    return media == "video" or "reel" in ptype


# ── Критерии ─────────────────────────────────────────────────────────────────

@dataclass
class CriteriaResult:
    reels_performance:    Optional[bool]   = None   # C1
    low_performing_reels: Optional[bool]   = None   # C2
    post_engagement:      Optional[bool]   = None   # C3
    monetization:         Optional[bool]   = None   # C4
    other_socials:        dict             = field(default_factory=dict)  # C5

    # Детали
    reels_90d_count:      int     = 0
    reels_above_150pct:   int     = 0
    bottom10_avg_views:   float   = 0.0
    engagement_rate_pct:  float   = 0.0
    total_interactions:   int     = 0
    monetization_signals: list    = field(default_factory=list)
    monetization_reason:  str     = ""
    offer_type:           str     = "unknown"
    offer_type_confidence: float  = 0.0
    funnel_type:          str     = "unknown"
    business_model:       str     = "unknown"
    audience_type:        str     = "unknown"
    monetization_strength: str    = "none"
    platform_mix:         str     = "instagram_only"
    primary_domain:       str     = ""
    bio_keywords:         list    = field(default_factory=list)
    cta_keywords:         list    = field(default_factory=list)
    language:             str     = "unknown"
    geo_hint:             str     = "unknown"
    icp:                  str     = "unknown"
    follower_count:       int     = 0
    posts_analyzed:       int     = 0
    analyzed_at:          str     = ""
    error:                str     = ""


def criterion_1_reels_performance(posts: list[dict], follower_count: int) -> tuple[bool, dict]:
    """≥5 Reels среди последних постов с views ≥ 150% подписчиков.

    NOTE: Xpoz не всегда возвращает createdAtDate. Когда даты есть —
    фильтруем по 90 дням; когда нет — смотрим по последним POSTS_LIMIT.
    """
    threshold = follower_count * 1.5
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=DAYS_90)
    has_dates = any(parse_date(p.get("createdAtDate")) for p in posts)

    qualifying = []
    total_reels = 0

    for p in posts:
        if not is_reel(p):
            continue
        dt = parse_date(p.get("createdAtDate"))
        if has_dates and dt and dt < cutoff:
            continue
        total_reels += 1
        views = safe_int(p.get("videoPlayCount"))
        date_str = str(dt.date()) if dt else "no_date"
        if views >= threshold:
            qualifying.append({"caption": str(p.get("caption", ""))[:60],
                                "views": views, "date": date_str})

    result = len(qualifying) >= 5
    note = "" if has_dates else "dates unavailable — checked in last fetched posts"
    return result, {
        "reels_90d_count": total_reels,
        "reels_above_150pct": len(qualifying),
        "threshold_views": int(threshold),
        "qualifying": qualifying,
        "note": note,
    }


def criterion_2_low_performing(posts: list[dict], follower_count: int) -> tuple[bool, dict]:
    """Avg нижних 10 из последних 15 Reels > 15% подписчиков."""
    threshold = follower_count * 0.15

    reels = [p for p in posts if is_reel(p) and p.get("videoPlayCount") is not None]

    if len(reels) < 10:
        return False, {"error": f"Недостаточно Reels: {len(reels)}/10"}

    last_15 = reels[:15]  # posts are already sorted by recency by Xpoz

    views_sorted = sorted(safe_int(p.get("videoPlayCount")) for p in last_15)
    bottom_10 = views_sorted[:10]
    avg = sum(bottom_10) / 10

    return avg > threshold, {
        "last_15_reels_count": len(last_15),
        "bottom10_views": bottom_10,
        "bottom10_avg": round(avg),
        "threshold_15pct": round(threshold),
    }


def criterion_3_engagement(posts: list[dict], follower_count: int) -> tuple[bool, dict]:
    """(likes+comments+reshares) / followers / 15 >= 1.5%."""
    posts_with_data = [p for p in posts if p.get("likeCount") is not None]
    last_15 = posts_with_data[:15]  # already sorted by recency

    if not last_15:
        return False, {"error": "Нет постов с данными"}

    total_likes    = sum(safe_int(p.get("likeCount"))    for p in last_15)
    total_comments = sum(safe_int(p.get("commentCount")) for p in last_15)
    total_reshares = sum(safe_int(p.get("reshareCount")) for p in last_15)
    total          = total_likes + total_comments + total_reshares

    avg_per_post   = total / len(last_15)
    rate           = avg_per_post / follower_count if follower_count > 0 else 0

    return rate >= 0.015, {
        "posts_count": len(last_15),
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_reshares": total_reshares,
        "total_interactions": total,
        "avg_per_post": round(avg_per_post),
        "engagement_rate_pct": round(rate * 100, 2),
        "note": "saves not available via API",
    }


def criterion_4_monetization(posts: list[dict], biography: str, external_url: str) -> tuple[bool, dict]:
    """Используем Claude Haiku с fallback taxonomy normalization."""
    captions = [str(p.get("caption", ""))[:200] for p in posts if p.get("caption")]
    captions_text = "\n---\n".join(captions[:20])
    content_text = f"Bio: {biography}\nExternal URL: {external_url}\n\nCaptions:\n{captions_text}"
    fallback = _heuristic_taxonomy(content_text)

    if not ANTHROPIC_KEY:
        fallback["error"] = "ANTHROPIC_API_KEY не задан, использован heuristic fallback"
        return fallback["has_signals"], fallback

    try:
        import anthropic
    except ImportError:
        fallback["error"] = "pip install anthropic"
        return fallback["has_signals"], fallback

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    try:
        response = client.messages.create(
            model=MODEL_HAIKU,
            max_tokens=384,
            system=[{
                "type": "text",
                "text": MONETIZATION_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": content_text,
            }],
        )
        raw = response.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        result_json = json.loads(raw[start:end]) if start >= 0 and end > start else {}

        usage = response.usage
        cost_input = usage.input_tokens * 0.80 / 1_000_000
        cost_output = usage.output_tokens * 4.00 / 1_000_000
        cache_read = getattr(usage, "cache_read_input_tokens", 0)
        cache_write = getattr(usage, "cache_creation_input_tokens", 0)

        normalized = normalize_taxonomy_result(
            result_json,
            biography=biography,
            external_url=external_url,
            other_socials={},
            captions_text=captions_text,
        )
        normalized["llm_tokens"] = {
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "cost_usd": round(cost_input + cost_output, 5),
        }
        normalized["raw_llm"] = result_json
        return normalized["has_signals"], normalized
    except Exception as e:
        fallback["error"] = str(e)
        return fallback["has_signals"], fallback


def criterion_5_other_socials(username: str, biography: str,
                               external_url: str) -> dict:
    """Ищем YouTube и Twitter/X без дополнительных API вызовов."""
    found = {}

    # YouTube из bio или external URL
    yt_signals = []
    if external_url and ("youtube.com" in external_url or "youtu.be" in external_url):
        yt_signals.append(external_url)

    # Bio может содержать ссылки
    for word in (biography or "").split():
        w = word.strip(".,!() ")
        if "youtube.com" in w or "youtu.be" in w:
            yt_signals.append(w)
        if "tiktok.com" in w or "@" in w and "tiktok" in w.lower():
            found["tiktok"] = w
        if "twitter.com" in w or "x.com" in w:
            found["twitter_bio"] = w
        if "linkedin.com" in w:
            found["linkedin"] = w
        if "t.me" in w or "telegram" in w.lower():
            found["telegram"] = w

    if yt_signals:
        found["youtube"] = yt_signals[0]

    # Twitter — попытка найти совпадающий хэндл
    twitter_data = fetch_twitter(username)
    if twitter_data and not twitter_data.get("error"):
        found["twitter"] = {
            "url": f"https://twitter.com/{twitter_data.get('username', username)}",
            "followers": safe_int(twitter_data.get("followersCount")),
            "verified": bool(twitter_data.get("isVerified")),
            "tweets": safe_int(twitter_data.get("tweetCount")),
        }

    return found


# ── Главная функция анализа ───────────────────────────────────────────────────

def analyze(username: str, verbose: bool = True) -> CriteriaResult:
    res = CriteriaResult(analyzed_at=datetime.utcnow().isoformat())
    username = username.lstrip("@")

    if verbose:
        print(f"\n{'═'*56}")
        print(f"  Анализ: @{username}")
        print(f"{'═'*56}")

    # ── Шаг 1: профиль (1 Xpoz result) ──────────────────────────
    if verbose: print("  [1/3] Загружаю профиль...", end=" ", flush=True)
    profile = fetch_profile(username)

    if profile.get("error") or not profile.get("username"):
        res.error = f"Профиль не найден: {profile.get('error', 'unknown')}"
        if verbose: print(f"❌ {res.error}")
        return res

    follower_count = safe_int(profile.get("followerCount"))
    biography      = str(profile.get("biography") or "")
    external_url   = str(profile.get("externalUrl") or "")
    ig_id          = profile.get("_ig_id") or profile.get("id")
    res.follower_count = follower_count

    realtime_mode = profile.get("_realtime", False)
    if verbose:
        rt_tag = " [real-time]" if realtime_mode else ""
        print(f"✓ {profile.get('fullName')} | {follower_count:,} подписчиков{rt_tag}")

    if follower_count == 0:
        res.error = "followerCount = 0, аккаунт приватный или не найден"
        if verbose: print(f"  ⚠ {res.error}")
        return res

    # ── Шаг 2: посты (1 Xpoz result × POSTS_LIMIT) ───────────────
    if verbose: print(f"  [2/3] Загружаю последние {POSTS_LIMIT} постов...", end=" ", flush=True)
    posts = fetch_posts(username, ig_id=ig_id if realtime_mode else None)
    res.posts_analyzed = len(posts)

    if verbose: print(f"✓ {len(posts)} постов получено")

    # ── Критерии 1–3 (чистый Python, 0 токенов) ──────────────────
    c1_result, c1_details = criterion_1_reels_performance(posts, follower_count)
    res.reels_performance   = c1_result
    res.reels_90d_count     = c1_details.get("reels_90d_count", 0)
    res.reels_above_150pct  = c1_details.get("reels_above_150pct", 0)

    c2_result, c2_details = criterion_2_low_performing(posts, follower_count)
    res.low_performing_reels = c2_result
    res.bottom10_avg_views   = c2_details.get("bottom10_avg", 0)

    c3_result, c3_details = criterion_3_engagement(posts, follower_count)
    res.post_engagement      = c3_result
    res.engagement_rate_pct  = c3_details.get("engagement_rate_pct", 0.0)
    res.total_interactions   = c3_details.get("total_interactions", 0)

    # ── Критерий 4 (Haiku + cache) ────────────────────────────────
    if verbose: print("  [3/3] Анализ монетизации (Haiku)...", end=" ", flush=True)
    c4_result, c4_details = criterion_4_monetization(posts, biography, external_url)

    if verbose:
        tokens = c4_details.get("llm_tokens", {})
        if tokens:
            cached = tokens.get("cache_read", 0)
            cost   = tokens.get("cost_usd", 0)
            print(f"✓ tokens={tokens.get('input')}, cache_hit={cached}, cost=${cost:.5f}")
        else:
            print(f"✓ {c4_details.get('error', '')}")

    # ── Критерий 5 (Twitter lookup = 1 Xpoz result) ────────────────
    res.other_socials = criterion_5_other_socials(username, biography, external_url)

    taxonomy = normalize_taxonomy_result(
        c4_details,
        biography=biography,
        external_url=external_url,
        other_socials=res.other_socials,
        captions_text="\n---\n".join(str(p.get("caption", ""))[:200] for p in posts if p.get("caption")),
    )
    res.monetization = c4_result or taxonomy["has_signals"]
    res.monetization_signals = taxonomy["signals_found"]
    res.monetization_reason = taxonomy["reasoning"]
    res.offer_type = taxonomy["offer_type"]
    res.offer_type_confidence = taxonomy["confidence"]
    res.funnel_type = taxonomy["funnel_type"]
    res.business_model = taxonomy["business_model"]
    res.audience_type = taxonomy["audience_type"]
    res.monetization_strength = taxonomy["monetization_strength"]
    res.platform_mix = taxonomy["platform_mix"]
    res.primary_domain = taxonomy["primary_domain"]
    res.bio_keywords = taxonomy["bio_keywords"]
    res.cta_keywords = taxonomy["cta_keywords"]
    res.language = taxonomy["language"]
    res.geo_hint = taxonomy["geo_hint"]
    res.icp = taxonomy["icp"]

    # ── Вывод результатов ─────────────────────────────────────────
    if verbose:
        _print_results(res, follower_count, c1_details, c2_details, c3_details)

    return res


def _print_results(res: CriteriaResult, fc: int,
                   c1: dict, c2: dict, c3: dict):
    def yn(v): return "TRUE  ✅" if v else "FALSE ❌"
    sep = "─" * 56

    print(f"\n{sep}")
    print(f"  РЕЗУЛЬТАТЫ — {fc:,} подписчиков")
    print(sep)

    # C1
    thr = int(fc * 1.5)
    print(f"\n  1. Reels performance (90д ≥150% views): {yn(res.reels_performance)}")
    print(f"     Порог: {thr:,} просмотров")
    print(f"     Reels в 90д: {res.reels_90d_count}, прошли порог: {res.reels_above_150pct}/5")
    for q in c1.get("qualifying", []):
        print(f"     ✓ {q['date']}  {q['views']:>10,}  {q['caption'][:40]}")

    # C2
    thr2 = int(fc * 0.15)
    print(f"\n  2. Low-performing Reels (bottom-10 avg):  {yn(res.low_performing_reels)}")
    print(f"     Avg bottom-10: {res.bottom10_avg_views:>8,.0f}  |  порог 15%: {thr2:,}")
    if c2.get("error"): print(f"     ⚠ {c2['error']}")

    # C3
    print(f"\n  3. Post engagement (last 15):             {yn(res.post_engagement)}")
    print(f"     Likes: {c3.get('total_likes',0):,}  Comments: {c3.get('total_comments',0):,}"
          f"  Reshares: {c3.get('total_reshares',0):,}")
    print(f"     Rate: {res.engagement_rate_pct:.2f}%  |  порог: 1.50%")

    # C4
    print(f"\n  4. Monetization signals:                  {yn(res.monetization)}")
    if res.monetization_signals:
        for s in res.monetization_signals[:5]:
            print(f"     • {s}")
    if res.monetization_reason:
        print(f"     {res.monetization_reason}")
    print(f"     offer={res.offer_type}  funnel={res.funnel_type}  strength={res.monetization_strength}")
    print(f"     audience={res.audience_type}  business={res.business_model}  icp={res.icp}")
    if res.primary_domain:
        print(f"     domain={res.primary_domain}  platform_mix={res.platform_mix}")
    if res.cta_keywords:
        print(f"     CTA: {', '.join(res.cta_keywords[:6])}")
    if res.bio_keywords:
        print(f"     Bio keywords: {', '.join(res.bio_keywords[:6])}")

    # C5
    print(f"\n  5. Other social media assets:")
    if not res.other_socials:
        print("     Не найдено")
    else:
        for platform, val in res.other_socials.items():
            if isinstance(val, dict):
                fol = val.get("followers", "")
                ver = " ✅" if val.get("verified") else ""
                print(f"     {platform:<12} {val.get('url','')}{ver}  ({fol:,} followers)" if fol else
                      f"     {platform:<12} {val.get('url','')}{ver}")
            else:
                print(f"     {platform:<12} {val}")
    print(f"\n{sep}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Анализ Instagram аккаунта по 5 критериям"
    )
    parser.add_argument("username", help="Instagram username (без @)")
    parser.add_argument("--json", action="store_true", help="Вывод в JSON")
    parser.add_argument("--save", action="store_true",
                        help="Сохранить результат в локальный SQLite (требует batch_analyze.py)")
    parser.add_argument("--quiet", action="store_true", help="Без verbose output")
    args = parser.parse_args()

    result = analyze(args.username, verbose=not args.quiet)

    if args.json or args.quiet:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))

    if args.save:
        try:
            from batch_analyze import save_result
            save_result(args.username, result)
            print(f"  ✓ Сохранено в SQLite")
        except ImportError:
            print("  ⚠ batch_analyze.py не найден", file=sys.stderr)

    return 0 if not result.error else 1


if __name__ == "__main__":
    sys.exit(main())
