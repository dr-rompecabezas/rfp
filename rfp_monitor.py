import argparse
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import yaml
from dotenv import load_dotenv

# Load environment variables from .env file for local development
load_dotenv()


CONFIG_PATH = Path("sources.yaml")
SEEN_PATH = Path("seen.json")
LOG_LEVEL_ENV = "LOG_LEVEL"
TRADE_DROP_WORDS = [
    "asphalt",
    "paving",
    "plow",
    "snow removal",
    "hvac",
    "plumbing",
    "flooring",
    "roof",
    "roofing",
    "janitorial",
    "cleaning",
    "welding",
    "fleet",
    "truck",
    "bus",
    "vehicle",
    "tree removal",
    "landscaping",
    "fencing",
    "doors",
    "windows",
    "supplies",
    "parts",
    "hardware",
    "concrete",
    "demolition",
    "construction",
    "road",
    "pavement",
]


TRUTHY_ENV = {"1", "true", "yes", "on"}
FALSY_ENV = {"0", "false", "no", "off"}


logger = logging.getLogger("rfp_monitor")


def env_flag(var_name: str) -> Optional[bool]:
    """Return True/False if env var is set to a recognizable boolean token."""
    raw = os.getenv(var_name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in TRUTHY_ENV:
        return True
    if normalized in FALSY_ENV:
        return False
    return None


def llm_enabled(llm_cfg: Dict[str, Any]) -> bool:
    """Resolve LLM toggle, letting an env var override the YAML setting."""
    env_var = llm_cfg.get("enabled_env", "LLM_ENABLED")
    env_value = env_flag(env_var)
    if env_value is not None:
        return env_value
    return bool(llm_cfg.get("enabled"))


def llm_model(llm_cfg: Dict[str, Any]) -> str:
    """Pick model, allowing an env override (model_env) before YAML."""
    env_var = llm_cfg.get("model_env", "LLM_MODEL")
    return os.getenv(env_var) or llm_cfg.get("model", "gpt-5-nano")


def configure_logging() -> None:
    level_name = os.getenv(LOG_LEVEL_ENV, "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    logger.debug("Logging initialized at %s", level_name)


@dataclass
class Source:
    name: str
    url: str
    include_keywords: List[str]
    exclude_keywords: List[str]


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not config or "sources" not in config:
        raise ValueError("Config file must contain a 'sources' list.")
    return config


def load_seen() -> Dict[str, List[str]]:
    if not SEEN_PATH.exists():
        return {}
    try:
        with SEEN_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # If file is corrupted, start fresh
        return {}


def save_seen(seen: Dict[str, List[str]]) -> None:
    with SEEN_PATH.open("w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2)


def normalize_text(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def fetch_source(source: Source) -> List[Dict[str, str]]:
    """Return list of matching links: [{'title': ..., 'url': ...}, ...]."""
    logger.info("Checking %s …", source.name)
    try:
        resp = requests.get(
            source.url,
            timeout=20,
            headers={"User-Agent": "rfp-monitor/1.0"},
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Error fetching %s: %s", source.url, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    matches: List[Dict[str, str]] = []

    include = [k.lower() for k in source.include_keywords]
    exclude = [k.lower() for k in source.exclude_keywords]

    for a in soup.find_all("a", href=True):
        href = urljoin(source.url, a["href"])
        parsed = urlparse(href)
        # Skip non-http(s), mailto, tel, and same-page anchors
        if parsed.scheme not in ("http", "https"):
            continue
        if not parsed.netloc:
            continue
        if href.endswith("#") or parsed.fragment:
            continue

        title = a.get_text(" ", strip=True) or href
        haystack = normalize_text(title + " " + href)

        if include and not any(k in haystack for k in include):
            continue
        if exclude and any(k in haystack for k in exclude):
            continue
        if trade_drop(title):
            continue

        matches.append({"title": title, "url": href})

    # Filter out low-quality navigation links
    filtered_matches = []
    for m in matches:
        url_lower = m["url"].lower()
        # Skip navigation/UI links
        if any(skip in url_lower for skip in [
            "/login", "/create", "/vendor/", "/français",
            "bidsandtenders.ca/#", "javascript:",
            "/home/bidshomepage", "/contact-", "/privacy", "/terms",
            "/supplier", "/buyer", "/plans", "/demo", "/subscribe"
        ]):
            continue
        filtered_matches.append(m)

    if len(matches) != len(filtered_matches):
        logger.debug("  Filtered out %d navigation links", len(matches) - len(filtered_matches))

    # Deduplicate by URL
    seen_urls = set()
    unique_matches = []
    for m in filtered_matches:
        if m["url"] not in seen_urls:
            seen_urls.add(m["url"])
            unique_matches.append(m)
    logger.info("  Found %d candidate links.", len(unique_matches))
    return unique_matches


def diff_new_items(
    source: Source,
    items: List[Dict[str, str]],
    seen: Dict[str, List[str]],
) -> List[Dict[str, str]]:
    known_urls = set(seen.get(source.name, []))
    new_items = [item for item in items if item["url"] not in known_urls]
    logger.debug("  Deduplication: %d current, %d previously seen, %d new",
                 len(items), len(known_urls), len(new_items))
    if new_items:
        all_urls = list(known_urls.union({item["url"] for item in new_items}))
        seen[source.name] = all_urls
    return new_items


def llm_filter(items: List[Dict[str, str]], llm_cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    # Skip LLM entirely if no items to process
    if not items:
        logger.debug("Skipping LLM filter: no items to process")
        return items

    api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
    model = llm_model(llm_cfg)
    rationale = llm_cfg.get("rationale", False)
    if not llm_enabled(llm_cfg):
        logger.info("LLM disabled via config/env; skipping LLM filtering.")
        return items

    api_key = os.getenv(api_key_env)
    if not api_key:
        logger.info("No API key found in env %s; skipping LLM filtering.", api_key_env)
        return items

    try:
        from openai import OpenAI
    except Exception:
        logger.warning("OpenAI SDK not available; skipping LLM filtering.")
        return items

    client = OpenAI(api_key=api_key)

    logger.info("Running LLM filter with model '%s' on %d items.", model, len(items))

    prompt_items = [
        {"index": idx, "title": item["title"], "url": item["url"]}
        for idx, item in enumerate(items)
    ]
    system = (
        "You triage procurement postings. Keep only opportunities that involve software, web, digital products, "
        "data/analytics, platforms, portals, LMS, edtech, or technical consulting. "
        "Drop construction, fleet/vehicles, physical supplies, janitorial, roads, HVAC, plumbing, landscaping, "
        "hardware-only, or general maintenance."
    )
    user = (
        "Classify each item as keep (true/false) and give a 1-line reason. "
        "Return a JSON object with a 'results' key containing a list like "
        "{\"results\":[{\"index\":0,\"keep\":true,\"reason\":\"...\"}]}.\n"
        f"Items:\n{prompt_items}"
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_completion_tokens=400,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
    except Exception as e:
        logger.error("LLM filter error: %s; skipping LLM filtering.", e)
        return items

    keep_items: List[Dict[str, str]] = []
    results = data.get("results", []) if isinstance(data, dict) else []
    for rec in results:
        try:
            idx = int(rec.get("index"))
            keep = bool(rec.get("keep"))
            reason = rec.get("reason", "")
        except Exception:
            continue
        if 0 <= idx < len(items) and keep:
            item = dict(items[idx])
            if rationale and reason:
                item["reason"] = reason
            keep_items.append(item)
    return keep_items


def format_report(new_items_by_source: Dict[str, List[Dict[str, str]]]) -> str:
    if not new_items_by_source:
        return "No new RFPs or calls for proposals found today."

    lines = []
    lines.append(f"RFP Monitor Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)
    for source_name, items in new_items_by_source.items():
        lines.append(f"\n{source_name}")
        lines.append("-" * len(source_name))
        for item in items:
            lines.append(f"- {item['title']}")
            if "reason" in item:
                lines.append(f"  Reason: {item['reason']}")
            lines.append(f"  {item['url']}")
    return "\n".join(lines)


def trade_drop(title: str) -> bool:
    hay = normalize_text(title)
    return any(word in hay for word in TRADE_DROP_WORDS)


def send_email(config: Dict[str, Any], subject: str, body: str) -> None:
    email_cfg = config.get("email", {})
    if not email_cfg.get("enabled"):
        logger.info("Email disabled in config; skipping email send.")
        return

    import smtplib
    from email.message import EmailMessage

    smtp_host = email_cfg["smtp_host"]
    smtp_port = email_cfg.get("smtp_port", 587)
    username = os.getenv(email_cfg.get("username_env", "")) or email_cfg["username"]
    password_env = email_cfg.get("password_env")
    password = os.getenv(password_env) if password_env else email_cfg.get("password")
    to_addr = email_cfg["to"]
    from_addr = email_cfg.get("from") or username

    if not password:
        logger.info("No SMTP password provided (password_env or password); skipping email send.")
        return

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(msg)
        logger.info("Email sent to %s.", to_addr)
    except Exception as e:
        logger.error("Error sending email: %s", e)


def main() -> None:
    parser = argparse.ArgumentParser(description='Monitor RFP sources')
    parser.add_argument('--force-refresh', action='store_true',
                       help='Ignore seen cache and process all items')
    args = parser.parse_args()

    configure_logging()
    config = load_config()

    # Handle force refresh
    if args.force_refresh:
        logger.info("Force refresh enabled: ignoring seen cache")
        SEEN_PATH.unlink(missing_ok=True)

    seen = load_seen()
    llm_cfg = config.get("llm", {})

    sources_cfg = config["sources"]
    sources = [
        Source(
            name=s["name"],
            url=s["url"],
            include_keywords=s.get("include_keywords", []),
            exclude_keywords=s.get("exclude_keywords", []),
        )
        for s in sources_cfg
    ]

    new_items_by_source: Dict[str, List[Dict[str, str]]] = {}

    for source in sources:
        items = fetch_source(source)
        new_items = diff_new_items(source, items, seen)
        # Only run LLM filter if there are new items
        if new_items:
            filtered = llm_filter(new_items, llm_cfg)
            if filtered:
                new_items_by_source[source.name] = filtered

    save_seen(seen)

    report = format_report(new_items_by_source)
    logger.info("\n%s", report)

    # Email summary (optional)
    if new_items_by_source:
        send_email(config, "New RFPs / Calls for Proposals", report)


if __name__ == "__main__":
    main()
