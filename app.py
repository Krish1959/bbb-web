"""
BBB-Web: Agentic Process Automation
Version: 5.0
------------------------------------
Step 1: Form > Scrape > Generate HeyGen Context > Push to GitHub > Email for Vetting
Debug mode: all output shown on the form page inline.
"""

import os
import re
import csv
import io
import json
import base64
import smtplib
import logging
import traceback
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request

VERSION = "5.0"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Krish1959/bbb-web")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_DATA_BRANCH = os.environ.get("GITHUB_DATA_BRANCH", "data")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)

APP_TITLE = os.environ.get("APP_TITLE", "LiveAvatar - Client Onboarding")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class DebugLog:
    def __init__(self):
        self.messages = []

    def info(self, msg):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"[{ts}] OK: {msg}"
        self.messages.append(entry)
        log.info(msg)

    def warn(self, msg):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"[{ts}] WARN: {msg}"
        self.messages.append(entry)
        log.warning(msg)

    def error(self, msg):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"[{ts}] ERROR: {msg}"
        self.messages.append(entry)
        log.error(msg)

    def text(self):
        return "\n".join(self.messages)


def normalize_url(raw):
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


def extract_short_name(url):
    parsed = urlparse(normalize_url(url))
    host = parsed.hostname or ""
    parts = host.replace("www.", "").split(".")
    return parts[0].lower() if parts else "unknown"


def scrape_website(url, dbg):
    url = normalize_url(url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    result = {
        "url": url, "title": "", "description": "",
        "text_blocks": [], "internal_links": [], "social_links": [],
        "emails_found": [], "phones_found": [], "error": None,
    }
    try:
        dbg.info(f"Fetching {url} ...")
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        dbg.info(f"Got HTTP {resp.status_code}, length={len(resp.text)}")
    except Exception as exc:
        result["error"] = str(exc)
        dbg.error(f"Scrape failed: {exc}")
        return result

    soup = BeautifulSoup(resp.text, "html.parser")
    parsed = urlparse(url)
    base_domain = parsed.hostname or ""

    if soup.title:
        result["title"] = soup.title.get_text(strip=True)
        dbg.info(f"Title: {result['title']}")

    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        result["description"] = meta["content"].strip()
    if not result["description"]:
        og = soup.find("meta", attrs={"property": "og:description"})
        if og and og.get("content"):
            result["description"] = og["content"].strip()

    for tag in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        txt = tag.get_text(strip=True)
        if len(txt) > 20:
            result["text_blocks"].append(txt)

    dbg.info(f"Extracted {len(result['text_blocks'])} text blocks")

    seen = set()
    social_domains = ["facebook.com", "twitter.com", "x.com", "linkedin.com",
                      "instagram.com", "youtube.com", "tiktok.com", "github.com"]
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(url, href)
        full_parsed = urlparse(full)
        if full_parsed.scheme not in ("http", "https"):
            if href.startswith("mailto:"):
                email_addr = href.replace("mailto:", "").split("?")[0]
                if email_addr not in result["emails_found"]:
                    result["emails_found"].append(email_addr)
            continue
        if full in seen:
            continue
        seen.add(full)
        host = (full_parsed.hostname or "").lower()
        if any(sd in host for sd in social_domains):
            result["social_links"].append(full)
            continue
        if base_domain.replace("www.", "") in host.replace("www.", ""):
            path = full_parsed.path.lower()
            if any(path.endswith(ext) for ext in [".jpg", ".png", ".pdf", ".svg", ".gif", ".zip"]):
                continue
            result["internal_links"].append(full)

    dbg.info(f"Found {len(result['internal_links'])} internal links, {len(result['social_links'])} social")

    priority_keywords = ["about", "service", "product", "contact", "team", "solution"]
    sub_pages = []
    for link in result["internal_links"]:
        lpath = urlparse(link).path.lower()
        if any(kw in lpath for kw in priority_keywords):
            sub_pages.append(link)
        if len(sub_pages) >= 5:
            break

    for sub_url in sub_pages:
        try:
            r2 = requests.get(sub_url, headers=headers, timeout=10)
            if r2.ok:
                s2 = BeautifulSoup(r2.text, "html.parser")
                for tag in s2.find_all(["h1", "h2", "h3", "p", "li"]):
                    txt = tag.get_text(strip=True)
                    if len(txt) > 20 and txt not in result["text_blocks"]:
                        result["text_blocks"].append(txt)
                page_text = s2.get_text()
                found_emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", page_text)
                for em in found_emails:
                    if em not in result["emails_found"]:
                        result["emails_found"].append(em)
                dbg.info(f"Sub-page OK: {sub_url}")
        except Exception:
            dbg.warn(f"Sub-page failed: {sub_url}")

    return result


def generate_heygen_context(form_data, scraped):
    company = form_data["company"]
    name = form_data["name"]
    email = form_data["email"]
    phone = form_data.get("phone", "")
    web_url = form_data["web_url"]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    site_title = scraped.get("title", company)
    site_desc = scraped.get("description", "")
    text_blocks = scraped.get("text_blocks", [])
    internal_links = scraped.get("internal_links", [])
    social_links = scraped.get("social_links", [])
    emails_found = scraped.get("emails_found", [])

    content_summary = ""
    char_count = 0
    for block in text_blocks:
        if char_count + len(block) > 2000:
            break
        content_summary += block + "\n\n"
        char_count += len(block)
    if not content_summary.strip():
        content_summary = f"{company} - information scraped from {normalize_url(web_url)}.\n"

    L = []
    L.append(f"# {company}\n")
    L.append(f"**Contact Person:** {name}")
    L.append(f"**Email:** {email}")
    if phone:
        L.append(f"**Phone:** {phone}")
    L.append(f"**Website:** {normalize_url(web_url)}")
    L.append(f"**Generated:** {timestamp}\n")
    L.append("---\n")
    L.append("## Opening Intro\n")
    L.append(site_desc if site_desc else f"{company} is an organization accessible at {normalize_url(web_url)}.")
    L.append("\n## Website Content Summary\n")
    if site_title:
        L.append(f"**Site Title:** {site_title}\n")
    L.append(content_summary.strip())
    L.append("\n## Links - Sub-pages & Associated URLs\n")
    if internal_links:
        for link in internal_links[:30]:
            L.append(f"- {link}")
    else:
        L.append("- No sub-pages discovered.")
    if social_links:
        L.append("\n## Social Media Links\n")
        for link in social_links:
            L.append(f"- {link}")
    if emails_found:
        L.append("\n## Contact Emails Found\n")
        for em in emails_found:
            L.append(f"- {em}")
    L.append("\n---\n")
    L.append("## PERSONA\n")
    L.append(f"You are a friendly and professional virtual assistant representing **{company}**.")
    L.append(f"Your role is to greet visitors, answer questions about {company}'s products and services,")
    L.append("and guide them to the right resources or team members.\n")
    L.append("You speak in a warm, conversational tone. You are helpful, concise, and knowledgeable")
    L.append(f"about everything related to {company}.\n")
    L.append("---\n")
    L.append("# KNOWLEDGE BASE\n")
    L.append(f"## About {company}\n")
    if site_desc:
        L.append(site_desc)
    L.append("\n### Key Information from Website\n")
    L.append(content_summary.strip())
    if internal_links:
        L.append("\n### Useful Pages to Reference\n")
        for link in internal_links[:15]:
            L.append(f"- {link}")
    L.append("\n---\n")
    L.append("# INSTRUCTIONS\n")
    L.append("Each response must be kept to 50 words maximum.\n")
    L.append("---\n")
    L.append("# COMMUNICATION STYLE\n")
    L.append("[Be concise]: Short, natural, no long monologues.")
    L.append("[Be conversational]: Sound human.")
    L.append("[Reply with warmth]: Make visitors comfortable.")
    L.append("[Be proactive]: Guide visitors to the information they need.")
    L.append("[Avoid listing]: Never speak in bullet points or numbers.\n")
    L.append("---\n")
    L.append("# RESPONSE GUIDELINES\n")
    L.append("- If audio is unclear, ask politely to repeat.")
    L.append("- Stay focused on company products, services, and information.")
    L.append("- Gently guide visitors who go off-topic.")
    L.append("- Never include stage directions like *smiles* or *nods*.\n")
    L.append("---\n")
    L.append("# JAILBREAKING\n")
    L.append("If visitors ask to play games or go off-topic, politely redirect:")
    L.append(f'> "I appreciate that! But let me help you with anything about {company} instead."')

    return "\n".join(L)


def github_api(method, path, json_data=None, params=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    return requests.request(method, url, headers=headers, json=json_data, params=params, timeout=20)


def ensure_data_branch(dbg):
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/refs/heads/{GITHUB_DATA_BRANCH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    dbg.info(f"Checking branch '{GITHUB_DATA_BRANCH}'...")
    resp = requests.get(api_url, headers=headers, timeout=15)
    if resp.status_code == 200:
        dbg.info(f"Branch '{GITHUB_DATA_BRANCH}' exists.")
        return True

    dbg.info(f"Branch not found (HTTP {resp.status_code}). Creating...")
    main_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/refs/heads/{GITHUB_BRANCH}"
    main_resp = requests.get(main_url, headers=headers, timeout=15)
    if main_resp.status_code != 200:
        dbg.error(f"Cannot find '{GITHUB_BRANCH}': HTTP {main_resp.status_code} - {main_resp.text[:200]}")
        return False

    main_sha = main_resp.json()["object"]["sha"]
    create_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/refs"
    create_resp = requests.post(create_url, headers=headers, json={
        "ref": f"refs/heads/{GITHUB_DATA_BRANCH}", "sha": main_sha,
    }, timeout=15)

    if create_resp.status_code in (200, 201):
        dbg.info(f"Branch '{GITHUB_DATA_BRANCH}' created.")
        return True
    else:
        dbg.error(f"Branch creation failed: HTTP {create_resp.status_code} - {create_resp.text[:200]}")
        return False


def github_get_file(path, branch=None):
    branch = branch or GITHUB_DATA_BRANCH
    resp = github_api("GET", path, params={"ref": branch})
    if resp.status_code == 200:
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]
    return None, None


def github_put_file(path, content, message, dbg, branch=None):
    branch = branch or GITHUB_DATA_BRANCH
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    existing_content, sha = github_get_file(path, branch=branch)
    payload = {"message": message, "content": encoded, "branch": branch}
    if sha:
        payload["sha"] = sha
        dbg.info(f"Updating: {path} (sha={sha[:8]})")
    else:
        dbg.info(f"Creating: {path}")

    resp = github_api("PUT", path, json_data=payload)
    if resp.status_code in (200, 201):
        dbg.info(f"GitHub OK: {path}")
        return True
    else:
        dbg.error(f"GitHub FAIL {path}: HTTP {resp.status_code} - {resp.text[:300]}")
        return False


def append_to_csv_on_github(form_data, dbg):
    csv_path = "submissions.csv"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    row = {
        "timestamp": timestamp, "name": form_data["name"],
        "company": form_data["company"], "email": form_data["email"],
        "phone": form_data.get("phone", ""), "web_url": form_data["web_url"],
    }
    headers_list = ["timestamp", "name", "company", "email", "phone", "web_url"]
    existing, sha = github_get_file(csv_path)
    if existing:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers_list)
        output.write(existing.rstrip("\n") + "\n")
        writer.writerow(row)
        new_content = output.getvalue()
        dbg.info("Appending to existing CSV")
    else:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers_list)
        writer.writeheader()
        writer.writerow(row)
        new_content = output.getvalue()
        dbg.info("Creating new CSV")
    return github_put_file(csv_path, new_content, f"Submission: {form_data['company']} - {timestamp}", dbg)


def push_context_to_github(short_name, context_content, dbg):
    file_path = f"Context/{short_name}.txt"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return github_put_file(file_path, context_content, f"Context: {short_name} - {timestamp}", dbg)


def send_vetting_email(to_email, company, context_content, dbg):
    if not SMTP_USER or not SMTP_PASS:
        dbg.warn("SMTP not configured. Skipping email.")
        return False

    dbg.info(f"Sending email to {to_email} via {SMTP_HOST}:{SMTP_PORT}...")
    subject = f"[LiveAvatar] HeyGen Context - {company} (Please Review)"
    body_html = f"""<html><body style="font-family:Arial,sans-serif;color:#333;">
    <h2>Hi,</h2>
    <p>Thank you for submitting details for <strong>{company}</strong>.</p>
    <p>Please review the generated context below and reply with corrections.</p>
    <hr/><pre style="background:#f5f5f5;padding:16px;border-radius:8px;font-size:13px;
    white-space:pre-wrap;">{context_content}</pre><hr/>
    <p>If everything looks good, no action needed.</p>
    <p>Best,<br/><strong>LiveAvatar Onboarding Team</strong></p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            dbg.info("SMTP TLS OK. Logging in...")
            server.login(SMTP_USER, SMTP_PASS)
            dbg.info("SMTP login OK. Sending...")
            server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        dbg.info(f"Email sent to {to_email}")
        return True
    except Exception as exc:
        dbg.error(f"Email FAILED: {type(exc).__name__}: {exc}")
        return False


# ── ROUTES ────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", title=APP_TITLE, errors=None, form=None,
                           debug_log="", version=VERSION, submitted=False)


@app.route("/submit", methods=["POST"])
def submit():
    dbg = DebugLog()
    dbg.info(f"=== BBB-Web v{VERSION} === Submit ===")

    form_data = {
        "name": request.form.get("name", "").strip(),
        "company": request.form.get("company", "").strip(),
        "email": request.form.get("email", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "web_url": request.form.get("web_url", "").strip(),
    }
    dbg.info(f"Form: {form_data['name']} | {form_data['company']} | {form_data['email']} | {form_data['web_url']}")

    errors = []
    if not form_data["name"]:
        errors.append("Name is required.")
    if not form_data["company"]:
        errors.append("Company is required.")
    if not form_data["email"] or "@" not in form_data["email"]:
        errors.append("Valid email required.")
    if not form_data["web_url"]:
        errors.append("Web URL is required.")

    if errors:
        dbg.error(f"Validation: {errors}")
        return render_template("index.html", title=APP_TITLE, errors=errors, form=form_data,
                               debug_log=dbg.text(), version=VERSION, submitted=False)

    dbg.info(f"GITHUB_TOKEN: {'SET (' + GITHUB_TOKEN[:8] + '...)' if GITHUB_TOKEN else 'NOT SET'}")
    dbg.info(f"GITHUB_REPO: {GITHUB_REPO}")
    dbg.info(f"SMTP_USER: {'SET' if SMTP_USER else 'NOT SET'}")

    # Step 1a: Scrape
    try:
        scraped = scrape_website(form_data["web_url"], dbg)
    except Exception as exc:
        dbg.error(f"Scrape CRASHED: {traceback.format_exc()}")
        scraped = {"url": form_data["web_url"], "title": "", "description": "",
                   "text_blocks": [], "internal_links": [], "social_links": [],
                   "emails_found": [], "phones_found": [], "error": str(exc)}

    # Step 1b: Generate context
    short_name = "unknown"
    context_content = ""
    try:
        context_content = generate_heygen_context(form_data, scraped)
        short_name = extract_short_name(form_data["web_url"])
        dbg.info(f"Context generated: '{short_name}' ({len(context_content)} chars)")
    except Exception as exc:
        dbg.error(f"Context gen CRASHED: {traceback.format_exc()}")

    # Step 1c: Data branch
    try:
        ensure_data_branch(dbg)
    except Exception as exc:
        dbg.error(f"Branch CRASHED: {traceback.format_exc()}")

    # Step 1d: CSV
    csv_ok = False
    try:
        csv_ok = append_to_csv_on_github(form_data, dbg)
    except Exception as exc:
        dbg.error(f"CSV CRASHED: {traceback.format_exc()}")

    # Step 1e: Context push
    ctx_ok = False
    try:
        ctx_ok = push_context_to_github(short_name, context_content, dbg)
    except Exception as exc:
        dbg.error(f"Context push CRASHED: {traceback.format_exc()}")

    # Step 1f: Email
    email_sent = False
    try:
        email_sent = send_vetting_email(form_data["email"], form_data["company"], context_content, dbg)
    except Exception as exc:
        dbg.error(f"Email CRASHED: {traceback.format_exc()}")

    dbg.info("=== SUMMARY ===")
    dbg.info(f"Scrape: {'OK' if not scraped.get('error') else 'FAIL'}")
    dbg.info(f"GitHub CSV: {'OK' if csv_ok else 'FAIL'}")
    dbg.info(f"GitHub Context: {'OK' if ctx_ok else 'FAIL'}")
    dbg.info(f"Email: {'OK' if email_sent else 'FAIL'}")
    dbg.info("=== DONE ===")

    return render_template("index.html", title=APP_TITLE, errors=None, form=form_data,
                           debug_log=dbg.text(), version=VERSION, submitted=True,
                           short_name=short_name, csv_ok=csv_ok, ctx_ok=ctx_ok,
                           email_sent=email_sent)


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "version": VERSION, "timestamp": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
