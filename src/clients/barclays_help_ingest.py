#!/usr/bin/env python3
"""
barclays_help_ingest.py

One-time (re-runnable) build script that ingests a CURATED set of the most useful
Barclays help-centre topics (see TOPICS below) into a dedicated Chroma collection
(`barclays_help` under `help_store/`), and ALSO writes every chunk to an Excel file
for inspection.

We deliberately do NOT crawl the whole help centre — only the high-value, common
end-user topics are embedded, which keeps retrieval focused and cheap.

This is OFFLINE only — it never runs in the request path. Re-running it is the refresh
mechanism. It does a full reset of the collection on each run.

Usage:
    python src/clients/barclays_help_ingest.py
    python src/clients/barclays_help_ingest.py --delay 0.5 --excel help_chunks.xlsx

Politeness: honours robots.txt, throttles between requests, sends a descriptive
User-Agent.
"""

import argparse
import os
import sys
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Make console output safe on Windows cp1252 terminals (emoji/symbols won't crash).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Make `src/` importable when run as `python src/clients/barclays_help_ingest.py`
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from utils.embeddings.help_store import HelpStore  # noqa: E402

BASE_DOMAIN = "www.barclays.co.uk"
HELP_PREFIX = "/help"
USER_AGENT = (
    "BarclaysHelpBot/1.0 (+chatbot knowledge-base ingestion; contact: admin)"
)
NOT_FOUND_TITLE = "That page can't be found"

# ---------------------------------------------------------------------------
# Curated top-25 personal-banking help topics (label, URL). These are the most
# common things an end user asks a banking chatbot. Edit this list to change what
# gets embedded — only these pages are stored. URLs validated against a prior crawl.
# ---------------------------------------------------------------------------
TOPICS: list[tuple[str, str]] = [
    # Cards & PIN
    ("Report a lost, stolen or damaged debit card",
     "https://www.barclays.co.uk/help/cards/debit-card/lost-stolen-damaged/"),
    ("Forgotten debit card PIN (get a reminder)",
     "https://www.barclays.co.uk/help/cards/pin/forgot-pin/"),
    ("Change your debit card PIN",
     "https://www.barclays.co.uk/help/cards/pin/change-pin/"),
    ("Debit card PIN locked or not working",
     "https://www.barclays.co.uk/help/cards/pin/pin-not-working/"),
    ("Using your debit card abroad",
     "https://www.barclays.co.uk/help/cards/using-cards/connect-abroad/"),
    ("Request a contactless debit card",
     "https://www.barclays.co.uk/help/cards/contactless/how-to-get/"),
    ("Change or turn off your contactless limit",
     "https://www.barclays.co.uk/help/cards/contactless/set-a-contactless-limit-in-app/"),
    ("View your card details in the app",
     "https://www.barclays.co.uk/help/cards/debit-card/view-card-details/"),
    ("What is the card security code (CVV)?",
     "https://www.barclays.co.uk/help/cards/debit-card/security-code/"),
    # Online banking
    ("Log in to Online Banking",
     "https://www.barclays.co.uk/help/online-banking/login/how-to/"),
    ("Lost or forgotten Online Banking log-in details",
     "https://www.barclays.co.uk/help/online-banking/login/forgot-details/"),
    ("Register for Online Banking",
     "https://www.barclays.co.uk/help/online-banking/registration/how-to/"),
    ("Set up your memorable word and passcode",
     "https://www.barclays.co.uk/help/online-banking/login/setup-passcode-word/"),
    # Mobile app
    ("Register for the Barclays app",
     "https://www.barclays.co.uk/help/mobile-banking/registration/how-to/"),
    ("Make a payment in the app",
     "https://www.barclays.co.uk/help/mobile-banking/using-mobile-banking/prembmb_payment/"),
    ("Lost or stolen phone with the Barclays app",
     "https://www.barclays.co.uk/help/mobile-banking/using-mobile-banking/new-lost-phone/"),
    # Payments & account services
    ("View your standing orders and Direct Debits",
     "https://www.barclays.co.uk/help/accounts/account-services/direct-debit-list/"),
    ("Find your sort code and account number",
     "https://www.barclays.co.uk/help/accounts/account-services/find-sort-code-app/"),
    ("Send money abroad (international payments)",
     "https://www.barclays.co.uk/help/international/payments/making-and-receiving-international-payments/send-money/"),
    ("Receive money from abroad",
     "https://www.barclays.co.uk/help/international/payments/making-and-receiving-international-payments/receive-money/"),
    ("IBANs and SWIFT codes",
     "https://www.barclays.co.uk/help/international/payments/making-and-receiving-international-payments/what-iban-swiftcode/"),
    # Statements, fees, complaints, overdraft
    ("View your online statements",
     "https://www.barclays.co.uk/help/accounts/statements-balances/how-can-i-view-online-statements-/"),
    ("Banking fees and charges",
     "https://www.barclays.co.uk/help/accounts/account-services/fees-charges/"),
    ("How to make a complaint",
     "https://www.barclays.co.uk/help/making-a-complaint/how-do-i-make-a-complaint/"),
    ("Cancel or reduce your overdraft",
     "https://www.barclays.co.uk/help/borrowing/overdrafts/cancel-reduce/"),
    # Fraud & scams (these live outside /help, under /fraud-and-scams/)
    ("Report fraud or a scam",
     "https://www.barclays.co.uk/fraud-and-scams/reporting-scams-and-reporting-fraud/"),
    ("Fraud and scams overview",
     "https://www.barclays.co.uk/fraud-and-scams/"),
    # Account opening & switching
    ("Open an account online",
     "https://www.barclays.co.uk/help/accounts/opening/how-to-open-an-account-online/"),
    ("Switch your account to Barclays",
     "https://www.barclays.co.uk/help/accounts/opening/how-to-switch-you-account/"),
    ("Open a joint current account",
     "https://www.barclays.co.uk/help/accounts/opening/open_joint/"),
    ("ID documents needed to open an account",
     "https://www.barclays.co.uk/help/accounts/opening/what-id/"),
    # Statements & cheques
    ("Order paper statements",
     "https://www.barclays.co.uk/help/accounts/statements-balances/order-statement/"),
    ("Order chequebooks and paying-in books",
     "https://www.barclays.co.uk/help/accounts/account-services/order-cheque-payingin/"),
    ("Cancel a lost or stolen chequebook",
     "https://www.barclays.co.uk/help/accounts/account-services/cancel-lost-chequebook/"),
    # Cards & Barclaycard
    ("Card captured by a cash machine",
     "https://www.barclays.co.uk/help/cards/using-cards/card-captured/"),
    ("When your debit card expires",
     "https://www.barclays.co.uk/help/cards/using-cards/when-new-card/"),
    ("What to do when your replacement debit card arrives",
     "https://www.barclays.co.uk/help/cards/debit-card/replacement-debit-card-arrival/"),
    ("Pay your Barclaycard bill",
     "https://www.barclays.co.uk/help/cards/barclaycard/pay-barclaycard-bill/"),
    ("Forgotten Barclaycard PIN",
     "https://www.barclays.co.uk/help/cards/barclaycard/forgotten-barclaycard-pin/"),
    ("Use your Barclaycard abroad",
     "https://www.barclays.co.uk/help/cards/barclaycard/use-my-card-abroad/"),
    # App & online banking
    ("Apple Pay – lost or stolen device",
     "https://www.barclays.co.uk/help/mobile-banking/Payments/apple-pay-lost-device/"),
    ("Generate a PINsentry code",
     "https://www.barclays.co.uk/help/mobile-banking/pinsentry/generate-code/"),
    ("Find your Online Banking membership number",
     "https://www.barclays.co.uk/help/online-banking/login/find-membership-number/"),
    ("Online Banking login problems",
     "https://www.barclays.co.uk/help/online-banking/login/problems/"),
    ("Use Online Banking abroad",
     "https://www.barclays.co.uk/help/online-banking/using-online-banking/abroad/"),
    ("Can't see an account in Online Banking",
     "https://www.barclays.co.uk/help/online-banking/using-online-banking/missing-account/"),
    # Protection & life events
    ("Is my money protected (FSCS)?",
     "https://www.barclays.co.uk/help/mobile-banking/using-mobile-banking/fscs/"),
    ("How safe is the Barclays app?",
     "https://www.barclays.co.uk/help/mobile-banking/using-mobile-banking/safe/"),
    ("Get a copy of your personal data",
     "https://www.barclays.co.uk/help/customer-services/personal-info-copy/"),
    ("Tell us someone has died (bereavement)",
     "https://www.barclays.co.uk/help/estates-bereavement/customer-death/"),
]


def _is_barclays_url(url: str) -> bool:
    """Curated topics are hand-picked, so we allow any barclays.co.uk page (not just
    /help) — e.g. the /fraud-and-scams/ section lives outside /help."""
    parsed = urlparse(url)
    return (
        parsed.scheme in ("http", "https")
        and parsed.netloc == BASE_DOMAIN
        and not parsed.query
    )


def _build_session() -> requests.Session:
    """Session with automatic retries/backoff for transient network errors."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        backoff_factor=1.0,  # 0s, 1s, 2s between retries
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _load_robots(session: requests.Session) -> RobotFileParser:
    rp = RobotFileParser()
    robots_url = f"https://{BASE_DOMAIN}/robots.txt"
    try:
        resp = session.get(robots_url, timeout=15)
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        else:
            rp.allow_all = True
    except Exception:
        rp.allow_all = True
    return rp


def _extract_content(soup: BeautifulSoup) -> tuple[str, str]:
    """Pull a clean title + main body text, dropping nav/footer/menu/script/style."""
    title = soup.title.get_text(strip=True) if soup.title else ""

    # Drop non-content elements by tag…
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form"]):
        tag.decompose()

    # …and by common chrome id/class hints (Barclays puts menus in plain divs).
    CHROME = ("nav", "menu", "header", "footer", "cookie", "breadcrumb",
              "skip", "banner", "search", "global", "masthead")
    for el in soup.find_all(True):
        try:
            cls = el.get("class") or []
            ident = el.get("id") or ""
        except (AttributeError, TypeError):
            continue  # element already removed as part of a decomposed parent
        hay = (" ".join(cls) + " " + ident).lower()
        if hay.strip() and any(h in hay for h in CHROME):
            el.decompose()

    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text(separator="\n", strip=True)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return title, "\n".join(lines)


def fetch_topics(topics: list[tuple[str, str]], delay: float, timeout: float) -> list[dict]:
    """Fetch ONLY the curated topic URLs. Returns [{topic, url, title, text}]."""
    session = _build_session()
    robots = _load_robots(session)
    pages: list[dict] = []

    for label, url in topics:
        if not _is_barclays_url(url):
            print(f"  ⏭️  not a barclays.co.uk URL, skipping: {url}")
            continue
        if hasattr(robots, "can_fetch") and not robots.can_fetch(USER_AGENT, url):
            print(f"  ⏭️  robots.txt disallows: {url}")
            continue

        try:
            resp = session.get(url, timeout=timeout)
            # Fix mojibake: trust the detected charset over the HTTP header default.
            if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or resp.encoding

            if resp.status_code != 200 or "text/html" not in resp.headers.get(
                "Content-Type", ""
            ):
                print(f"  ⚠️  skipped (status {resp.status_code}): {label}")
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            title, text = _extract_content(soup)

            if NOT_FOUND_TITLE.lower() in (title or "").lower() or len(text) < 200:
                print(f"  ⚠️  no content (404 or empty): {label} -> {url}")
                continue

            pages.append({"topic": label, "url": url, "title": title, "text": text})
            print(f"  ✅ [{len(pages)}] {label}  ({len(text)} chars)")
        except Exception as e:
            print(f"  ⚠️  fetch failed {label}: {type(e).__name__}: {e}")

        time.sleep(delay)

    print(f"\nFetched {len(pages)} of {len(topics)} curated topics.")
    return pages


def chunk_pages(pages: list[dict]) -> list[dict]:
    """Split each page into chunks. Returns a flat list of chunk records."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    records: list[dict] = []
    for page in pages:
        chunks = splitter.split_text(page["text"])
        for i, chunk in enumerate(chunks):
            records.append({
                "chunk_id": f"{page['url']}#{i}",
                "topic": page["topic"],
                "title": page["title"],
                "source_url": page["url"],
                "chunk_index": i,
                "char_count": len(chunk),
                "text": chunk,
            })
    return records


def write_excel(records: list[dict], path: str) -> None:
    """Write every chunk (one row each) to an Excel workbook."""
    df = pd.DataFrame(records, columns=[
        "chunk_id", "topic", "title", "source_url", "chunk_index", "char_count", "text",
    ])
    df.to_excel(path, index=False, engine="openpyxl")
    print(f"📄 Wrote {len(df)} chunk rows to {path}")


def main():
    parser = argparse.ArgumentParser(description="Ingest curated Barclays help topics into Chroma + Excel.")
    parser.add_argument("--delay", type=float, default=0.7, help="seconds between requests")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request read timeout (s)")
    parser.add_argument("--persist-dir", default="help_store")
    parser.add_argument("--collection", default="barclays_help")
    parser.add_argument("--excel", default="help_chunks.xlsx", help="Excel output path for chunks")
    args = parser.parse_args()

    print(f"Fetching {len(TOPICS)} curated help topics ({args.delay}s delay)…\n")
    pages = fetch_topics(TOPICS, args.delay, args.timeout)
    if not pages:
        print("No pages fetched — aborting (nothing to embed).")
        return

    records = chunk_pages(pages)
    print(f"Chunked into {len(records)} chunks.")

    # 1) Excel snapshot of every chunk
    write_excel(records, args.excel)

    # 2) Embed + store in Chroma
    texts = [r["text"] for r in records]
    metadatas = [
        {
            "topic": r["topic"],
            "title": r["title"],
            "source_url": r["source_url"],
            "chunk_index": r["chunk_index"],
        }
        for r in records
    ]

    store = HelpStore(persist_directory=args.persist_dir, collection_name=args.collection)
    print("🧹 Resetting existing collection…")
    store.reset()
    store.add_documents(texts, metadatas)

    print(f"✅ Done. Stored {store.count()} chunks in '{args.collection}' at {args.persist_dir}/")


if __name__ == "__main__":
    main()
