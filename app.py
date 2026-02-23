"""
BBB-Web: Agentic Process Automation
------------------------------------
Step 1: Form → Scrape → Generate HeyGen Context → Push to GitHub → Email for Vetting
Hosted on Render.com | Code on GitHub: Krish1959/bbb-web
"""

import os
import re
import csv
import io
import json
import base64
import smtplib
import logging
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, redirect, url_for, flash

# ── Config ────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Krish1959/bbb-web")  # owner/repo
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)

APP_TITLE = os.environ.get("APP_TITLE", "LiveAvatar – Client Onboarding")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════

def normalize_url(raw: str) -> str:
    """Ensure URL has a scheme."""
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


def extract_short_name(url: str) -> str:
    """
    Extract a clean short name from a URL.
    e.g. https://www.bescon.sg → bescon
    """
    parsed = urlparse(normalize_url(url))
    host = parsed.hostname or ""
    # Remove www. and TLD
    parts = host.replace("www.", "").split(".")
    if parts:
        return parts[0].lower()
    return "unknown"


# ── Web Scraping ──────────────────────────────────────────────────────

def scrape_website(url: str) -> dict:
    """
    Scrape a website and return structured data:
    - title, description, main text
    - all internal links (sub-pages)
    - social links
    """
    url = normalize_url(url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    result = {
        "url": url,
        "title": "",
        "description": "",
        "text_blocks": [],
        "internal_links": [],
        "social_links": [],
        "emails_found": [],
        "phones_found": [],
        "error": None,
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        result["error"] = str(exc)
        return result

    soup = BeautifulSoup(resp.text, "html.parser")
    parsed = urlparse(url)
    base_domain = parsed.hostname or ""

    # Title
    if soup.title:
        result["title"] = soup.title.get_text(strip=True)

    # Meta description
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        result["description"] = meta["content"].strip()

    # OG description fallback
    if not result["description"]:
        og = soup.find("meta", attrs={"property": "og:description"})
        if og and og.get("content"):
            result["description"] = og["content"].strip()

    # Text blocks (paragraphs, headings)
    for tag in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        txt = tag.get_text(strip=True)
        if len(txt) > 20:
            result["text_blocks"].append(txt)

    # Links
    seen = set()
    social_domains = ["facebook.com", "twitter.com", "x.com", "linkedin.com",
                      "instagram.com", "youtube.com", "tiktok.com", "github.com"]

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(url, href)
        full_parsed = urlparse(full)

        if full_parsed.scheme not in ("http", "https"):
            # Check mailto
            if href.startswith("mailto:"):
                email = href.replace("mailto:", "").split("?")[0]
                if email not in result["emails_found"]:
                    result["emails_found"].append(email)
            continue

        if full in seen:
            continue
        seen.add(full)

        host = (full_parsed.hostname or "").lower()

        # Social link?
        if any(sd in host for sd in social_domains):
            result["social_links"].append(full)
            continue

        # Internal link?
        if base_domain.replace("www.", "") in host.replace("www.", ""):
            # Skip anchors, images, files
            path = full_parsed.path.lower()
            if any(path.endswith(ext) for ext in [".jpg", ".png", ".pdf", ".svg", ".gif", ".zip"]):
                continue
            result["internal_links"].append(full)

    # Also try to scrape key sub-pages (About, Services, Contact) – max 5
    priority_keywords = ["about", "service", "product", "contact", "team", "solution"]
    sub_pages_to_scrape = []
    for link in result["internal_links"]:
        lpath = urlparse(link).path.lower()
        if any(kw in lpath for kw in priority_keywords):
            sub_pages_to_scrape.append(link)
        if len(sub_pages_to_scrape) >= 5:
            break

    for sub_url in sub_pages_to_scrape:
        try:
            r2 = requests.get(sub_url, headers=headers, timeout=10)
            if r2.ok:
                s2 = BeautifulSoup(r2.text, "html.parser")
                for tag in s2.find_all(["h1", "h2", "h3", "p", "li"]):
                    txt = tag.get_text(strip=True)
                    if len(txt) > 20 and txt not in result["text_blocks"]:
                        result["text_blocks"].append(txt)
                # Grab any emails on sub-pages
                page_text = s2.get_text()
                found_emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", page_text)
                for em in found_emails:
                    if em not in result["emails_found"]:
                        result["emails_found"].append(em)
        except Exception:
            pass

    # Extract phone numbers from full text
    full_text = soup.get_text()
    phones = re.findall(r"[\+]?[\d\s\-\(\)]{8,15}", full_text)
    for p in phones:
        cleaned = re.sub(r"\s+", "", p)
        if len(cleaned) >= 8 and cleaned not in result["phones_found"]:
            result["phones_found"].append(p.strip())
            if len(result["phones_found"]) >= 5:
                break

    return result


# ── Context File Generator ────────────────────────────────────────────

def generate_heygen_context(form_data: dict, scraped: dict) -> str:
    """
    Build a HeyGen-compatible context file in Markdown format.
    """
    company = form_data["company"]
    name = form_data["name"]
    email = form_data["email"]
    phone = form_data.get("phone", "")
    web_url = form_data["web_url"]
    short_name = extract_short_name(web_url)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    site_title = scraped.get("title", company)
    site_desc = scraped.get("description", "")
    text_blocks = scraped.get("text_blocks", [])
    internal_links = scraped.get("internal_links", [])
    social_links = scraped.get("social_links", [])
    emails_found = scraped.get("emails_found", [])

    # Build the content summary from scraped text (limit to ~2000 chars)
    content_summary = ""
    char_count = 0
    for block in text_blocks:
        if char_count + len(block) > 2000:
            break
        content_summary += block + "\n\n"
        char_count += len(block)

    if not content_summary.strip():
        content_summary = f"{company} — information scraped from {web_url}.\n"

    lines = []
    lines.append(f"# {company}")
    lines.append("")
    lines.append(f"**Contact Person:** {name}")
    lines.append(f"**Email:** {email}")
    if phone:
        lines.append(f"**Phone:** {phone}")
    lines.append(f"**Website:** {normalize_url(web_url)}")
    lines.append(f"**Generated:** {timestamp}")
    lines.append("")

    # ── Opening Intro ──
    lines.append("---")
    lines.append("")
    lines.append("## Opening Intro")
    lines.append("")
    if site_desc:
        lines.append(f"{site_desc}")
    else:
        lines.append(f"{company} is an organization accessible at {normalize_url(web_url)}.")
    lines.append("")

    # ── Website Content Summary ──
    lines.append("## Website Content Summary")
    lines.append("")
    if site_title:
        lines.append(f"**Site Title:** {site_title}")
        lines.append("")
    lines.append(content_summary.strip())
    lines.append("")

    # ── Sub-domain / Pages ──
    lines.append("## Links – Sub-pages & Associated URLs")
    lines.append("")
    if internal_links:
        for link in internal_links[:30]:
            lines.append(f"- {link}")
    else:
        lines.append("- No sub-pages discovered during scrape.")
    lines.append("")

    # ── Social Links ──
    if social_links:
        lines.append("## Social Media Links")
        lines.append("")
        for link in social_links:
            lines.append(f"- {link}")
        lines.append("")

    # ── Contact Info Found ──
    if emails_found:
        lines.append("## Contact Emails Found on Site")
        lines.append("")
        for em in emails_found:
            lines.append(f"- {em}")
        lines.append("")

    # ══════════════════════════════════════════════════════════════════
    #  FULL PROMPT (HeyGen Avatar Context)
    # ══════════════════════════════════════════════════════════════════

    lines.append("---")
    lines.append("")
    lines.append("## PERSONA")
    lines.append("")
    lines.append(f"You are a friendly and professional virtual assistant representing **{company}**.")
    lines.append(f"Your role is to greet visitors, answer questions about {company}'s products and services, ")
    lines.append("and guide them to the right resources or team members.")
    lines.append("")
    lines.append("You speak in a warm, conversational tone. You are helpful, concise, and knowledgeable ")
    lines.append(f"about everything related to {company}.")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("# KNOWLEDGE BASE")
    lines.append("")
    lines.append(f"## About {company}")
    lines.append("")
    if site_desc:
        lines.append(site_desc)
    lines.append("")
    lines.append("### Key Information from Website")
    lines.append("")
    lines.append(content_summary.strip())
    lines.append("")

    if internal_links:
        lines.append("### Useful Pages to Reference")
        lines.append("")
        for link in internal_links[:15]:
            lines.append(f"- {link}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("# INSTRUCTIONS")
    lines.append("")
    lines.append("Each response must be kept to 50 words maximum.")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("# COMMUNICATION STYLE")
    lines.append("")
    lines.append("[Be concise]: Short, natural, no long monologues.")
    lines.append("[Be conversational]: Sound human — use light fillers where appropriate.")
    lines.append("[Reply with warmth]: Make visitors comfortable; show genuine interest.")
    lines.append("[Be proactive]: Guide visitors to the information they need.")
    lines.append("[Avoid listing]: Never speak in bullet points or numbers.")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("# RESPONSE GUIDELINES")
    lines.append("")
    lines.append("- If audio is unclear, ask politely to repeat.")
    lines.append("- Stay focused on the company's products, services, and information.")
    lines.append("- Gently guide visitors who go off-topic.")
    lines.append("- Never include stage directions like *smiles* or *nods*.")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("# JAILBREAKING")
    lines.append("")
    lines.append("If visitors ask to play games, \"pretend,\" or go off-topic, politely redirect:")
    lines.append(f'> "I appreciate that! But let me help you with anything about {company} instead."')
    lines.append("")

    return "\n".join(lines)


# ── GitHub Operations ─────────────────────────────────────────────────

def github_api(method: str, path: str, json_data: dict = None):
    """Generic GitHub API call."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    resp = requests.request(method, url, headers=headers, json=json_data, timeout=20)
    return resp


def github_get_file(path: str):
    """Get file content + SHA from GitHub."""
    resp = github_api("GET", path)
    if resp.status_code == 200:
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]
    return None, None


def github_put_file(path: str, content: str, message: str):
    """Create or update a file on GitHub."""
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    existing_content, sha = github_get_file(path)

    payload = {
        "message": message,
        "content": encoded,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = github_api("PUT", path, json_data=payload)
    if resp.status_code in (200, 201):
        log.info(f"✅ GitHub: {path} pushed successfully.")
        return True
    else:
        log.error(f"❌ GitHub push failed for {path}: {resp.status_code} {resp.text}")
        return False


def append_to_csv_on_github(form_data: dict):
    """
    Append a row to submissions.csv on GitHub.
    If file doesn't exist, create with headers.
    """
    csv_path = "submissions.csv"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    row = {
        "timestamp": timestamp,
        "name": form_data["name"],
        "company": form_data["company"],
        "email": form_data["email"],
        "phone": form_data.get("phone", ""),
        "web_url": form_data["web_url"],
    }
    headers_list = ["timestamp", "name", "company", "email", "phone", "web_url"]

    existing, sha = github_get_file(csv_path)

    if existing:
        # Append row
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers_list)
        output.write(existing.rstrip("\n") + "\n")
        writer.writerow(row)
        new_content = output.getvalue()
    else:
        # Create new file with header
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers_list)
        writer.writeheader()
        writer.writerow(row)
        new_content = output.getvalue()

    return github_put_file(csv_path, new_content, f"Add submission: {form_data['company']} – {timestamp}")


def push_context_to_github(short_name: str, context_content: str):
    """Push the context .txt file to Context/ folder on GitHub."""
    file_path = f"Context/{short_name}.txt"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return github_put_file(
        file_path,
        context_content,
        f"Add context for {short_name} – {timestamp}"
    )


# ── Email ─────────────────────────────────────────────────────────────

def send_vetting_email(to_email: str, company: str, context_content: str):
    """Send the generated context to the client's email for vetting."""
    if not SMTP_USER or not SMTP_PASS:
        log.warning("⚠️  SMTP not configured — skipping email.")
        return False

    subject = f"[LiveAvatar] Your HeyGen Context File – {company} (Please Review)"

    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;">
    <h2>Hi,</h2>
    <p>Thank you for submitting your details for <strong>{company}</strong>.</p>
    <p>We've generated a draft context file for your LiveAvatar / HeyGen setup.
    Please review the content below and reply with any corrections or additions.</p>
    <hr/>
    <pre style="background:#f5f5f5;padding:16px;border-radius:8px;font-size:13px;
    overflow-x:auto;white-space:pre-wrap;">{context_content}</pre>
    <hr/>
    <p>If everything looks good, no action is needed — we'll proceed with onboarding.</p>
    <p>Best regards,<br/><strong>LiveAvatar Onboarding Team</strong></p>
    </body></html>
    """

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
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        log.info(f"✅ Email sent to {to_email}")
        return True
    except Exception as exc:
        log.error(f"❌ Email failed: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", title=APP_TITLE, errors=None, form=None)


@app.route("/submit", methods=["POST"])
def submit():
    form_data = {
        "name": request.form.get("name", "").strip(),
        "company": request.form.get("company", "").strip(),
        "email": request.form.get("email", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "web_url": request.form.get("web_url", "").strip(),
    }

    # ── Validation ──
    errors = []
    if not form_data["name"]:
        errors.append("Name is required.")
    if not form_data["company"]:
        errors.append("Company is required.")
    if not form_data["email"] or "@" not in form_data["email"]:
        errors.append("A valid email is required.")
    if not form_data["web_url"]:
        errors.append("Web URL is required.")

    if errors:
        return render_template("index.html", title=APP_TITLE, errors=errors, form=form_data)

    # ── Step 1a: Scrape the website ──
    log.info(f"🔍 Scraping {form_data['web_url']}...")
    try:
        scraped = scrape_website(form_data["web_url"])
    except Exception as exc:
        log.error(f"Scrape crashed: {exc}")
        scraped = {"url": form_data["web_url"], "title": "", "description": "",
                   "text_blocks": [], "internal_links": [], "social_links": [],
                   "emails_found": [], "phones_found": [], "error": str(exc)}

    if scraped.get("error"):
        log.warning(f"Scrape had errors: {scraped['error']}")

    # ── Step 1b: Generate HeyGen Context File ──
    context_content = generate_heygen_context(form_data, scraped)
    short_name = extract_short_name(form_data["web_url"])

    # ── Step 1c: Push submissions.csv to GitHub ──
    csv_ok = False
    try:
        csv_ok = append_to_csv_on_github(form_data)
    except Exception as exc:
        log.error(f"CSV push failed: {exc}")

    # ── Step 1d: Push context file to GitHub ──
    ctx_ok = False
    try:
        ctx_ok = push_context_to_github(short_name, context_content)
    except Exception as exc:
        log.error(f"Context push failed: {exc}")

    # ── Step 1e: Send email for human vetting ──
    email_sent = False
    try:
        email_sent = send_vetting_email(form_data["email"], form_data["company"], context_content)
    except Exception as exc:
        log.error(f"Email send crashed: {exc}")

    return render_template(
        "success.html",
        title=APP_TITLE,
        form=form_data,
        short_name=short_name,
        email_sent=email_sent,
        github_ok=csv_ok and ctx_ok,
    )


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
