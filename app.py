"""
BBB-Web: Agentic Process Automation
Version: 5.2
"""

import os
import re
import csv
import io
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

VERSION = "5.2"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bbb-web-secret")

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
        self.entries = []

    def add(self, level, msg):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.entries.append({"ts": ts, "level": level, "msg": msg})
        getattr(log, level, log.info)(msg)

    def ok(self, msg):
        self.add("info", msg)

    def warn(self, msg):
        self.add("warning", msg)

    def err(self, msg):
        self.add("error", msg)

    def text(self):
        lines = []
        for e in self.entries:
            tag = {"info": "OK", "warning": "WARN", "error": "ERROR"}.get(e["level"], "?")
            lines.append(f"[{e['ts']}] {tag}: {e['msg']}")
        return "\n".join(lines)


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
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"}
    result = {"url": url, "title": "", "description": "", "text_blocks": [],
              "internal_links": [], "social_links": [], "emails_found": [], "error": None}
    try:
        dbg.ok(f"Fetching {url}")
        resp = requests.get(url, headers=hdrs, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        dbg.ok(f"HTTP {resp.status_code}, {len(resp.text)} bytes")
    except Exception as exc:
        result["error"] = str(exc)
        dbg.err(f"Fetch failed: {exc}")
        return result

    soup = BeautifulSoup(resp.text, "html.parser")
    parsed = urlparse(url)
    base_domain = parsed.hostname or ""

    if soup.title:
        result["title"] = soup.title.get_text(strip=True)
        dbg.ok(f"Title: {result['title']}")

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
    dbg.ok(f"{len(result['text_blocks'])} text blocks extracted")

    seen = set()
    socials = ["facebook.com", "twitter.com", "x.com", "linkedin.com",
               "instagram.com", "youtube.com", "tiktok.com", "github.com"]
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(url, href)
        fp = urlparse(full)
        if fp.scheme not in ("http", "https"):
            if href.startswith("mailto:"):
                em = href.replace("mailto:", "").split("?")[0]
                if em not in result["emails_found"]:
                    result["emails_found"].append(em)
            continue
        if full in seen:
            continue
        seen.add(full)
        host = (fp.hostname or "").lower()
        if any(s in host for s in socials):
            result["social_links"].append(full)
        elif base_domain.replace("www.", "") in host.replace("www.", ""):
            if not any(fp.path.lower().endswith(e) for e in [".jpg", ".png", ".pdf", ".svg", ".gif", ".zip"]):
                result["internal_links"].append(full)
    dbg.ok(f"{len(result['internal_links'])} internal links, {len(result['social_links'])} social")

    kw = ["about", "service", "product", "contact", "team", "solution"]
    subs = [l for l in result["internal_links"] if any(k in urlparse(l).path.lower() for k in kw)][:5]
    for su in subs:
        try:
            r2 = requests.get(su, headers=hdrs, timeout=10)
            if r2.ok:
                s2 = BeautifulSoup(r2.text, "html.parser")
                for tag in s2.find_all(["h1", "h2", "h3", "p", "li"]):
                    txt = tag.get_text(strip=True)
                    if len(txt) > 20 and txt not in result["text_blocks"]:
                        result["text_blocks"].append(txt)
                for em in re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", s2.get_text()):
                    if em not in result["emails_found"]:
                        result["emails_found"].append(em)
                dbg.ok(f"Sub-page OK: {su}")
        except Exception:
            dbg.warn(f"Sub-page fail: {su}")
    return result


def generate_heygen_context(form_data, scraped):
    co = form_data["company"]
    nm = form_data["name"]
    em = form_data["email"]
    ph = form_data.get("phone", "")
    wu = normalize_url(form_data["web_url"])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sd = scraped.get("description", "")
    st = scraped.get("title", co)
    tb = scraped.get("text_blocks", [])
    il = scraped.get("internal_links", [])
    sl = scraped.get("social_links", [])
    ef = scraped.get("emails_found", [])

    cs = ""
    cc = 0
    for b in tb:
        if cc + len(b) > 2000:
            break
        cs += b + "\n\n"
        cc += len(b)
    if not cs.strip():
        cs = f"{co} - info from {wu}\n"

    L = [f"# {co}\n", f"**Contact:** {nm}", f"**Email:** {em}"]
    if ph:
        L.append(f"**Phone:** {ph}")
    L += [f"**Website:** {wu}", f"**Generated:** {ts}\n", "---\n", "## Opening Intro\n",
          sd if sd else f"{co} is accessible at {wu}.",
          "\n## Website Content Summary\n", f"**Site Title:** {st}\n" if st else "", cs.strip(),
          "\n## Links - Sub-pages\n"]
    L += [f"- {l}" for l in il[:30]] if il else ["- No sub-pages found."]
    if sl:
        L += ["\n## Social Media\n"] + [f"- {l}" for l in sl]
    if ef:
        L += ["\n## Emails Found\n"] + [f"- {e}" for e in ef]
    L += ["\n---\n", "## PERSONA\n",
          f"You are a friendly virtual assistant for **{co}**.",
          f"Answer questions about {co}'s products/services and guide visitors.\n",
          "---\n", "# KNOWLEDGE BASE\n", f"## About {co}\n", sd if sd else "",
          "\n### Key Info\n", cs.strip()]
    if il:
        L += ["\n### Pages\n"] + [f"- {l}" for l in il[:15]]
    L += ["\n---\n", "# INSTRUCTIONS\n", "Keep responses under 50 words.\n",
          "---\n", "# COMMUNICATION STYLE\n",
          "[Be concise] [Be conversational] [Reply with warmth] [Be proactive] [Avoid listing]\n",
          "---\n", "# RESPONSE GUIDELINES\n",
          "- Ask to repeat if unclear.", "- Stay on-topic.", "- No stage directions.\n",
          "---\n", "# JAILBREAKING\n",
          f'If off-topic: "Let me help you with {co} instead."']
    return "\n".join(L)


def github_api(method, path, json_data=None, params=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    h = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    return requests.request(method, url, headers=h, json=json_data, params=params, timeout=20)


def ensure_data_branch(dbg):
    h = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    dbg.ok(f"Checking branch '{GITHUB_DATA_BRANCH}'")
    r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/git/refs/heads/{GITHUB_DATA_BRANCH}",
                     headers=h, timeout=15)
    if r.status_code == 200:
        dbg.ok("Branch exists")
        return True
    dbg.ok(f"Branch missing (HTTP {r.status_code}), creating from '{GITHUB_BRANCH}'")
    mr = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/git/refs/heads/{GITHUB_BRANCH}",
                      headers=h, timeout=15)
    if mr.status_code != 200:
        dbg.err(f"Main branch not found: HTTP {mr.status_code} {mr.text[:200]}")
        return False
    sha = mr.json()["object"]["sha"]
    cr = requests.post(f"https://api.github.com/repos/{GITHUB_REPO}/git/refs", headers=h,
                       json={"ref": f"refs/heads/{GITHUB_DATA_BRANCH}", "sha": sha}, timeout=15)
    if cr.status_code in (200, 201):
        dbg.ok("Branch created")
        return True
    dbg.err(f"Branch create failed: HTTP {cr.status_code} {cr.text[:200]}")
    return False


def gh_get(path, branch=None):
    branch = branch or GITHUB_DATA_BRANCH
    r = github_api("GET", path, params={"ref": branch})
    if r.status_code == 200:
        d = r.json()
        return base64.b64decode(d["content"]).decode("utf-8"), d["sha"]
    return None, None


def gh_put(path, content, msg, dbg, branch=None):
    branch = branch or GITHUB_DATA_BRANCH
    enc = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    _, sha = gh_get(path, branch)
    payload = {"message": msg, "content": enc, "branch": branch}
    if sha:
        payload["sha"] = sha
    dbg.ok(f"Pushing {path} to '{branch}' {'(update)' if sha else '(new)'}")
    r = github_api("PUT", path, json_data=payload)
    if r.status_code in (200, 201):
        dbg.ok(f"GitHub OK: {path}")
        return True
    dbg.err(f"GitHub FAIL {path}: HTTP {r.status_code} {r.text[:300]}")
    return False


def csv_push(form_data, dbg):
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S UTC")

    fields = ["Sl_No", "Date", "Time", "Name", "Company", "Email", "Phone", "Web_URL"]
    existing, _ = gh_get("submissions.csv")

    # Calculate next Sl_No
    sl_no = 1
    if existing:
        lines = existing.strip().split("\n")
        sl_no = len(lines)  # header is line 1, so data rows = len-1, next = len

    row = {
        "Sl_No": sl_no,
        "Date": date_str,
        "Time": time_str,
        "Name": form_data["name"],
        "Company": form_data["company"],
        "Email": form_data["email"],
        "Phone": form_data.get("phone", ""),
        "Web_URL": form_data["web_url"],
    }

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    if existing and existing.startswith("Sl_No,"):
        buf.write(existing.rstrip("\n") + "\n")
        dbg.ok(f"Appending to CSV (Sl_No={sl_no})")
    else:
        if existing:
            dbg.warn("Old CSV format detected — resetting with new headers")
        w.writeheader()
        dbg.ok("Creating new CSV with headers: Sl_No, Date, Time, Name, Company, Email, Phone, Web_URL")
    w.writerow(row)
    return gh_put("submissions.csv", buf.getvalue(), f"Submission #{sl_no}: {form_data['company']} - {date_str}", dbg)


def ctx_push(short_name, ctx, dbg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return gh_put(f"Context/{short_name}.txt", ctx, f"Context: {short_name} - {ts}", dbg)


def send_email(to, company, ctx, dbg):
    if not SMTP_USER or not SMTP_PASS:
        dbg.warn("SMTP not configured, skipping email")
        return False
    dbg.ok(f"Emailing {to} via {SMTP_HOST}:{SMTP_PORT}")
    subj = f"[LiveAvatar] HeyGen Context - {company}"
    html = f"""<html><body style="font-family:Arial;color:#333">
    <h2>Hi,</h2><p>Review the context for <b>{company}</b>:</p><hr/>
    <pre style="background:#f5f5f5;padding:16px;font-size:13px;white-space:pre-wrap">{ctx}</pre>
    <hr/><p>Reply with corrections or approve.</p>
    <p>Best,<br/><b>LiveAvatar Team</b></p></body></html>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subj
    msg["From"] = FROM_EMAIL
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            dbg.ok("TLS OK, logging in...")
            s.login(SMTP_USER, SMTP_PASS)
            dbg.ok("Login OK, sending...")
            s.sendmail(FROM_EMAIL, [to], msg.as_string())
        dbg.ok(f"Email sent to {to}")
        return True
    except Exception as exc:
        dbg.err(f"Email FAIL: {type(exc).__name__}: {exc}")
        return False


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", title=APP_TITLE, version=VERSION, errors=None, form=None)


@app.route("/submit", methods=["POST"])
def submit():
    dbg = DebugLog()
    dbg.ok(f"BBB-Web v{VERSION} - Submit received")

    fd = {
        "name": request.form.get("name", "").strip(),
        "company": request.form.get("company", "").strip(),
        "email": request.form.get("email", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "web_url": request.form.get("web_url", "").strip(),
    }
    dbg.ok(f"Form: {fd['name']} | {fd['company']} | {fd['email']} | {fd['web_url']}")

    errs = []
    if not fd["name"]: errs.append("Name required")
    if not fd["company"]: errs.append("Company required")
    if not fd["email"] or "@" not in fd["email"]: errs.append("Valid email required")
    if not fd["web_url"]: errs.append("Web URL required")
    if errs:
        dbg.err(f"Validation: {errs}")
        return render_template("debug.html", version=VERSION, debug_log=dbg.text(),
                               submitted=False, errors=errs)

    dbg.ok(f"GITHUB_TOKEN: {'YES (' + GITHUB_TOKEN[:8] + '...)' if GITHUB_TOKEN else 'NOT SET'}")
    dbg.ok(f"GITHUB_REPO: {GITHUB_REPO}")
    dbg.ok(f"SMTP_USER: {'YES' if SMTP_USER else 'NOT SET'}")

    # 1. Scrape
    try:
        scraped = scrape_website(fd["web_url"], dbg)
    except Exception:
        dbg.err(f"Scrape CRASH:\n{traceback.format_exc()}")
        scraped = {"url": fd["web_url"], "title": "", "description": "", "text_blocks": [],
                   "internal_links": [], "social_links": [], "emails_found": [], "error": "crashed"}

    # 2. Context
    short_name = "unknown"
    ctx = ""
    try:
        ctx = generate_heygen_context(fd, scraped)
        short_name = extract_short_name(fd["web_url"])
        dbg.ok(f"Context: '{short_name}' ({len(ctx)} chars)")
    except Exception:
        dbg.err(f"Context CRASH:\n{traceback.format_exc()}")

    # 3. Branch
    try:
        ensure_data_branch(dbg)
    except Exception:
        dbg.err(f"Branch CRASH:\n{traceback.format_exc()}")

    # 4. CSV
    csv_ok = False
    try:
        csv_ok = csv_push(fd, dbg)
    except Exception:
        dbg.err(f"CSV CRASH:\n{traceback.format_exc()}")

    # 5. Context push
    ctx_ok = False
    try:
        ctx_ok = ctx_push(short_name, ctx, dbg)
    except Exception:
        dbg.err(f"Ctx push CRASH:\n{traceback.format_exc()}")

    # 6. Email
    email_ok = False
    try:
        email_ok = send_email(fd["email"], fd["company"], ctx, dbg)
    except Exception:
        dbg.err(f"Email CRASH:\n{traceback.format_exc()}")

    dbg.ok("=== SUMMARY ===")
    dbg.ok(f"Scrape: {'OK' if not scraped.get('error') else 'FAIL'}")
    dbg.ok(f"GitHub CSV: {'OK' if csv_ok else 'FAIL'}")
    dbg.ok(f"GitHub Context: {'OK' if ctx_ok else 'FAIL'}")
    dbg.ok(f"Email: {'OK' if email_ok else 'FAIL'}")
    dbg.ok("=== DONE ===")

    return render_template("debug.html", version=VERSION, debug_log=dbg.text(),
                           submitted=True, errors=None, csv_ok=csv_ok, ctx_ok=ctx_ok,
                           email_ok=email_ok, short_name=short_name, form=fd)


@app.route("/health")
def health():
    return {"status": "ok", "version": VERSION}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))