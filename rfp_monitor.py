import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import yaml


CONFIG_PATH = Path("sources.yaml")
SEEN_PATH = Path("seen.json")
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
    print(f"Checking {source.name} …")
    try:
        resp = requests.get(
            source.url,
            timeout=20,
            headers={"User-Agent": "rfp-monitor/1.0"},
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  ! Error fetching {source.url}: {e}")
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

    # Deduplicate by URL
    seen_urls = set()
    unique_matches = []
    for m in matches:
        if m["url"] not in seen_urls:
            seen_urls.add(m["url"])
            unique_matches.append(m)
    print(f"  Found {len(unique_matches)} candidate links.")
    return unique_matches


def diff_new_items(
    source: Source,
    items: List[Dict[str, str]],
    seen: Dict[str, List[str]],
) -> List[Dict[str, str]]:
    known_urls = set(seen.get(source.name, []))
    new_items = [item for item in items if item["url"] not in known_urls]
    if new_items:
        all_urls = list(known_urls.union({item["url"] for item in new_items}))
        seen[source.name] = all_urls
    return new_items


def llm_filter(items: List[Dict[str, str]], llm_cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
    model = llm_cfg.get("model", "gpt-4o-mini")
    rationale = llm_cfg.get("rationale", False)
    if not llm_enabled(llm_cfg):
        return items

    api_key = os.getenv(api_key_env)
    if not api_key:
        return items

    try:
        from openai import OpenAI
    except Exception:
        print("OpenAI SDK not available; skipping LLM filtering.")
        return items

    client = OpenAI(api_key=api_key)

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
        "Return JSON list like [{\"index\":0,\"keep\":true,\"reason\":\"...\"}].\n"
        f"Items:\n{prompt_items}"
    )

    try:
        resp = client.responses.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            max_output_tokens=400,
        )
        content = resp.output[0].content[0].text  # type: ignore
        data = json.loads(content)
    except Exception as e:
        print(f"LLM filter error: {e}; skipping LLM filtering.")
        return items

    keep_items: List[Dict[str, str]] = []
    for rec in data if isinstance(data, list) else []:
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
        print("Email disabled in config; skipping email send.")
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
        print("No SMTP password provided (password_env or password); skipping email send.")
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
        print(f"Email sent to {to_addr}.")
    except Exception as e:
        print(f"Error sending email: {e}")


def main() -> None:
    config = load_config()
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
        filtered = llm_filter(new_items, llm_cfg)
        if new_items:
            new_items_by_source[source.name] = filtered

    save_seen(seen)

    report = format_report(new_items_by_source)
    print("\n" + report)

    # Email summary (optional)
    if new_items_by_source:
        send_email(config, "New RFPs / Calls for Proposals", report)


if __name__ == "__main__":
    main()
