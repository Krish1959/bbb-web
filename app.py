"""
BBB-Web: Agentic Process Automation
Version: 6.0
"""

import os, re, csv, io, base64, smtplib, logging, traceback
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request

VERSION = "6.0"
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bbb-web-secret")

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO   = os.environ.get("GITHUB_REPO", "Krish1959/bbb-web")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_DATA_BRANCH = os.environ.get("GITHUB_DATA_BRANCH", "data")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)
APP_TITLE  = os.environ.get("APP_TITLE", "Avatar Chat - Client Onboarding")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Debug Logger ──────────────────────────────────────────────────────
class DebugLog:
    def __init__(self):
        self.entries = []
    def ok(self, m):
        t = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.entries.append(f"[{t}] OK: {m}"); log.info(m)
    def warn(self, m):
        t = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.entries.append(f"[{t}] WARN: {m}"); log.warning(m)
    def err(self, m):
        t = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.entries.append(f"[{t}] ERROR: {m}"); log.error(m)
    def text(self):
        return "\n".join(self.entries)

# ── Helpers ───────────────────────────────────────────────────────────
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

# ── Scrape-Protection Check ───────────────────────────────────────────
def check_protection(url, dbg):
    """Returns (is_protected, reason)."""
    url = normalize_url(url)
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"}
    try:
        r = requests.get(url, headers=hdrs, timeout=15, allow_redirects=True)
        if r.status_code == 403:
            return True, "HTTP 403 Forbidden – site blocks automated access"
        if r.status_code == 503:
            return True, "HTTP 503 – likely Cloudflare / DDoS protection"
        if r.status_code == 429:
            return True, "HTTP 429 – rate limited"
        hl = r.text.lower()
        hits = []
        if "cf-browser-verification" in hl or ("cloudflare" in hl and "challenge" in hl):
            hits.append("Cloudflare challenge")
        if "recaptcha" in hl or "g-recaptcha" in hl:
            hits.append("reCAPTCHA")
        if "hcaptcha" in hl or "h-captcha" in hl:
            hits.append("hCaptcha")
        if "captcha" in hl and not any(x in hl for x in ["recaptcha","hcaptcha"]):
            hits.append("CAPTCHA")
        if "datadome" in hl:
            hits.append("DataDome")
        if "perimeterx" in hl:
            hits.append("PerimeterX")
        soup = BeautifulSoup(r.text, "html.parser")
        if len(soup.get_text(strip=True)) < 100 and r.status_code == 200:
            hits.append("Very short page (possible block)")
        if hits:
            return True, "; ".join(hits)
        return False, "No protection detected"
    except requests.exceptions.SSLError:
        return True, "SSL certificate error"
    except requests.exceptions.ConnectionError:
        return True, "Connection refused"
    except requests.exceptions.Timeout:
        return True, "Timeout – site may block bots"
    except Exception as e:
        return True, f"Check failed: {e}"

# ── Web Scraping (BeautifulSoup) ──────────────────────────────────────
def scrape_website(url, dbg):
    url = normalize_url(url)
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"}
    result = {"url": url, "title": "", "description": "", "text_blocks": [],
              "internal_links": [], "social_links": [], "emails_found": [],
              "error": None, "protected": False, "protection_reason": ""}

    prot, reason = check_protection(url, dbg)
    result["protected"] = prot
    result["protection_reason"] = reason
    if prot:
        dbg.warn(f"PROTECTION: {reason}")
    else:
        dbg.ok(f"Protection check: {reason}")

    try:
        dbg.ok(f"Fetching {url}")
        resp = requests.get(url, headers=hdrs, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        dbg.ok(f"HTTP {resp.status_code}, {len(resp.text)} bytes")
    except Exception as e:
        result["error"] = str(e)
        dbg.err(f"Fetch failed: {e}")
        return result

    soup = BeautifulSoup(resp.text, "html.parser")
    parsed = urlparse(url)
    base = parsed.hostname or ""

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

    for tag in soup.find_all(["h1","h2","h3","p","li"]):
        txt = tag.get_text(strip=True)
        if len(txt) > 20:
            result["text_blocks"].append(txt)
    dbg.ok(f"{len(result['text_blocks'])} text blocks")

    if result["protected"] and len(result["text_blocks"]) < 3:
        dbg.warn("Very little content – protection likely effective")

    seen = set()
    socials = ["facebook.com","twitter.com","x.com","linkedin.com",
               "instagram.com","youtube.com","tiktok.com","github.com"]
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(url, href)
        fp = urlparse(full)
        if fp.scheme not in ("http","https"):
            if href.startswith("mailto:"):
                em = href.replace("mailto:","").split("?")[0]
                if em not in result["emails_found"]:
                    result["emails_found"].append(em)
            continue
        if full in seen: continue
        seen.add(full)
        host = (fp.hostname or "").lower()
        if any(s in host for s in socials):
            result["social_links"].append(full)
        elif base.replace("www.","") in host.replace("www.",""):
            if not any(fp.path.lower().endswith(e) for e in [".jpg",".png",".pdf",".svg",".gif",".zip"]):
                result["internal_links"].append(full)
    dbg.ok(f"{len(result['internal_links'])} internal, {len(result['social_links'])} social")

    kw = ["about","service","product","contact","team","solution"]
    subs = [l for l in result["internal_links"] if any(k in urlparse(l).path.lower() for k in kw)][:5]
    for su in subs:
        try:
            r2 = requests.get(su, headers=hdrs, timeout=10)
            if r2.ok:
                s2 = BeautifulSoup(r2.text, "html.parser")
                for tag in s2.find_all(["h1","h2","h3","p","li"]):
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

# ── Context Generator ─────────────────────────────────────────────────
def generate_context(form_data, scraped):
    co = form_data["company"]; nm = form_data["name"]; em = form_data["email"]
    ph = form_data.get("phone",""); wu = normalize_url(form_data["web_url"])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sd = scraped.get("description",""); st = scraped.get("title", co)
    tb = scraped.get("text_blocks",[]); il = scraped.get("internal_links",[])
    sl = scraped.get("social_links",[]); ef = scraped.get("emails_found",[])

    cs, cc = "", 0
    for b in tb:
        if cc + len(b) > 2000: break
        cs += b + "\n\n"; cc += len(b)
    if not cs.strip():
        cs = f"{co} - info from {wu}\n"

    L = [f"# {co}\n", f"**Contact:** {nm}", f"**Email:** {em}"]
    if ph: L.append(f"**Phone:** {ph}")
    L += [f"**Website:** {wu}", f"**Generated:** {ts}\n", "---\n",
          "## Opening Intro\n", sd if sd else f"{co} is accessible at {wu}.",
          "\n## Website Content Summary\n"]
    if st: L.append(f"**Site Title:** {st}\n")
    L += [cs.strip(), "\n## Links - Sub-pages\n"]
    L += [f"- {l}" for l in il[:30]] if il else ["- No sub-pages found."]
    if sl: L += ["\n## Social Media\n"] + [f"- {l}" for l in sl]
    if ef: L += ["\n## Emails Found\n"] + [f"- {e}" for e in ef]
    L += ["\n---\n", "## PERSONA\n",
          f"You are a friendly virtual assistant for **{co}**.",
          f"Answer questions about {co}'s products/services and guide visitors.\n",
          "---\n", "# KNOWLEDGE BASE\n", f"## About {co}\n",
          sd if sd else "", "\n### Key Info\n", cs.strip()]
    if il: L += ["\n### Pages\n"] + [f"- {l}" for l in il[:15]]
    L += ["\n---\n", "# INSTRUCTIONS\n", "Keep responses under 50 words.\n",
          "---\n", "# COMMUNICATION STYLE\n",
          "[Be concise] [Be conversational] [Reply with warmth] [Be proactive] [Avoid listing]\n",
          "---\n", "# RESPONSE GUIDELINES\n",
          "- Ask to repeat if unclear.", "- Stay on-topic.", "- No stage directions.\n",
          "---\n", "# JAILBREAKING\n",
          f'If off-topic: "Let me help you with {co} instead."']
    return "\n".join(L)

# ── GitHub Ops ────────────────────────────────────────────────────────
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
        dbg.ok("Branch exists"); return True
    dbg.ok(f"Branch missing (HTTP {r.status_code}), creating...")
    mr = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/git/refs/heads/{GITHUB_BRANCH}",
                      headers=h, timeout=15)
    if mr.status_code != 200:
        dbg.err(f"Main branch not found: {mr.status_code}"); return False
    sha = mr.json()["object"]["sha"]
    cr = requests.post(f"https://api.github.com/repos/{GITHUB_REPO}/git/refs", headers=h,
                       json={"ref": f"refs/heads/{GITHUB_DATA_BRANCH}", "sha": sha}, timeout=15)
    if cr.status_code in (200, 201):
        dbg.ok("Branch created"); return True
    dbg.err(f"Branch create fail: {cr.status_code}"); return False

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
    if sha: payload["sha"] = sha
    dbg.ok(f"Pushing {path} to '{branch}' {'(update)' if sha else '(new)'}")
    r = github_api("PUT", path, json_data=payload)
    if r.status_code in (200, 201):
        dbg.ok(f"GitHub OK: {path}"); return True
    dbg.err(f"GitHub FAIL {path}: HTTP {r.status_code} {r.text[:300]}"); return False

def csv_push(form_data, dbg):
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d"); time_str = now.strftime("%H:%M:%S UTC")
    fields = ["Sl_No","Date","Time","Name","Company","Email","Phone","Web_URL","Avatar_Type"]
    existing, _ = gh_get("submissions.csv")
    sl_no = 1
    if existing and existing.startswith("Sl_No,"):
        sl_no = len(existing.strip().split("\n"))
    row = {"Sl_No": sl_no, "Date": date_str, "Time": time_str,
           "Name": form_data["name"], "Company": form_data["company"],
           "Email": form_data["email"], "Phone": form_data.get("phone",""),
           "Web_URL": form_data["web_url"],
           "Avatar_Type": form_data.get("avatar_type","type1")}
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=fields)
    if existing and existing.startswith("Sl_No,") and "Avatar_Type" in existing.split("\n")[0]:
        buf.write(existing.rstrip("\n") + "\n")
        dbg.ok(f"Appending CSV (Sl_No={sl_no})")
    else:
        if existing: dbg.warn("Old CSV format – resetting")
        w.writeheader(); dbg.ok("New CSV with headers")
    w.writerow(row)
    return gh_put("submissions.csv", buf.getvalue(), f"Submission #{sl_no}: {form_data['company']} - {date_str}", dbg)

def ctx_push(short_name, ctx, dbg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return gh_put(f"Context/{short_name}.txt", ctx, f"Context: {short_name} - {ts}", dbg)

# ── Email ─────────────────────────────────────────────────────────────
def send_email(to, company, ctx, dbg):
    if not SMTP_USER or not SMTP_PASS:
        dbg.warn("SMTP not configured, skipping"); return False
    dbg.ok(f"Emailing {to} via {SMTP_HOST}:{SMTP_PORT}")
    subj = f"[Web Scrapped] Context for Avatar Chat - {company}"
    html = f"""<html><body style="font-family:Arial;color:#333">
    <h2>Hi,</h2>
    <p>Review the context for your Avatar to be Constructed for <b>{company}</b>:</p><hr/>
    <pre style="background:#f5f5f5;padding:16px;font-size:13px;white-space:pre-wrap">{ctx}</pre>
    <hr/><p>Please review and reply with corrections or approval.</p>
    <p>Best,<br/><b>Avatar Onboarding Team</b></p></body></html>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subj; msg["From"] = FROM_EMAIL; msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            dbg.ok("TLS OK, logging in...")
            s.login(SMTP_USER, SMTP_PASS); dbg.ok("Login OK, sending...")
            s.sendmail(FROM_EMAIL, [to], msg.as_string())
        dbg.ok(f"Email sent to {to}"); return True
    except Exception as e:
        dbg.err(f"Email FAIL: {type(e).__name__}: {e}"); return False

# ══════════════════════════════════════════════════════════════════════
#  STAGE-2 PLACEHOLDER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════

def Post_Original(context_data, form_data, dbg):
    """Stage-2 Type-1: Push scraped data for standard avatar."""
    dbg.ok(f">>> Post_Original invoked for '{form_data['company']}' (Type-1)")
    dbg.ok(f"    Context length: {len(context_data)} chars")
    dbg.ok(f"    Avatar Type: Type-1 (Standard)")
    # TODO: Integrate with Avatar API here
    dbg.ok("    [PLACEHOLDER] – Stage-2 API call goes here")
    return True

def Post_Advanced(context_data, form_data, dbg):
    """Stage-2 Type-2: Push scraped data for advanced avatar."""
    dbg.ok(f">>> Post_Advanced invoked for '{form_data['company']}' (Type-2)")
    dbg.ok(f"    Context length: {len(context_data)} chars")
    dbg.ok(f"    Avatar Type: Type-2 (Advanced)")
    # TODO: Integrate with Advanced Avatar API here
    dbg.ok("    [PLACEHOLDER] – Stage-2 Advanced API call goes here")
    return True

# ══════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", title=APP_TITLE, version=VERSION, errors=None, form=None)

@app.route("/submit", methods=["POST"])
def submit():
    dbg = DebugLog()
    dbg.ok(f"BBB-Web v{VERSION} – Submit")

    fd = {
        "name":     request.form.get("name","").strip(),
        "company":  request.form.get("company","").strip(),
        "email":    request.form.get("email","").strip(),
        "phone":    request.form.get("phone","").strip(),
        "web_url":  request.form.get("web_url","").strip(),
        "avatar_type": request.form.get("avatar_type","type1"),
    }
    dbg.ok(f"Form: {fd['name']} | {fd['company']} | {fd['email']} | {fd['web_url']} | avatar={fd['avatar_type']}")

    errs = []
    if not fd["name"]: errs.append("Name required")
    if not fd["company"]: errs.append("Company required")
    if not fd["email"] or "@" not in fd["email"]: errs.append("Valid email required")
    if not fd["web_url"]: errs.append("Web URL required")
    if errs:
        dbg.err(f"Validation: {errs}")
        return render_template("debug.html", version=VERSION, debug_log=dbg.text(),
                               submitted=False, errors=errs)

    dbg.ok(f"GITHUB_TOKEN: {'YES ('+GITHUB_TOKEN[:8]+'...)' if GITHUB_TOKEN else 'NOT SET'}")
    dbg.ok(f"SMTP_USER: {'YES' if SMTP_USER else 'NOT SET'}")

    # 1. Scrape
    scraped = {"error": "not run"}
    try:
        scraped = scrape_website(fd["web_url"], dbg)
    except Exception:
        dbg.err(f"Scrape CRASH:\n{traceback.format_exc()}")
        scraped = {"url": fd["web_url"], "title":"", "description":"", "text_blocks":[],
                   "internal_links":[], "social_links":[], "emails_found":[],
                   "error":"crashed", "protected":False, "protection_reason":""}

    # 2. Context
    short_name = "unknown"; ctx = ""
    try:
        ctx = generate_context(fd, scraped)
        short_name = extract_short_name(fd["web_url"])
        dbg.ok(f"Context: '{short_name}' ({len(ctx)} chars)")
    except Exception:
        dbg.err(f"Context CRASH:\n{traceback.format_exc()}")

    # 3. GitHub
    try: ensure_data_branch(dbg)
    except Exception: dbg.err(f"Branch CRASH:\n{traceback.format_exc()}")

    csv_ok = False
    try: csv_ok = csv_push(fd, dbg)
    except Exception: dbg.err(f"CSV CRASH:\n{traceback.format_exc()}")

    ctx_ok = False
    try: ctx_ok = ctx_push(short_name, ctx, dbg)
    except Exception: dbg.err(f"Ctx CRASH:\n{traceback.format_exc()}")

    dbg.ok("=== Scrape & GitHub Done ===")

    is_protected = scraped.get("protected", False)
    prot_reason  = scraped.get("protection_reason", "")

    return render_template("review.html", version=VERSION, debug_log=dbg.text(),
                           ctx=ctx, short_name=short_name, form=fd,
                           csv_ok=csv_ok, ctx_ok=ctx_ok,
                           is_protected=is_protected, prot_reason=prot_reason)

@app.route("/send-email", methods=["POST"])
def send_email_route():
    dbg = DebugLog()
    dbg.ok(f"v{VERSION} – Send Email")
    to = request.form.get("email","").strip()
    company = request.form.get("company","").strip()
    ctx = request.form.get("context","")
    short_name = request.form.get("short_name","")

    if ctx and short_name:
        try:
            ensure_data_branch(dbg)
            ctx_push(short_name, ctx, dbg)
        except Exception:
            dbg.err(f"Ctx update CRASH:\n{traceback.format_exc()}")

    email_ok = False
    try: email_ok = send_email(to, company, ctx, dbg)
    except Exception: dbg.err(f"Email CRASH:\n{traceback.format_exc()}")

    dbg.ok(f"Email: {'OK' if email_ok else 'FAIL'}")
    return render_template("debug.html", version=VERSION, debug_log=dbg.text(),
                           submitted=True, errors=None, csv_ok=True, ctx_ok=True,
                           email_ok=email_ok, short_name=short_name,
                           form={"email":to,"company":company})

@app.route("/post-data", methods=["POST"])
def post_data():
    dbg = DebugLog()
    dbg.ok(f"v{VERSION} – Post Data")

    company    = request.form.get("company","").strip()
    email      = request.form.get("email","").strip()
    short_name = request.form.get("short_name","")
    ctx        = request.form.get("context","")
    avatar_type = request.form.get("avatar_type","type1")
    send_email_flag = request.form.get("send_email","0")

    fd = {"company": company, "email": email, "name": "", "web_url": "",
          "avatar_type": avatar_type}

    dbg.ok(f"Company: {company}, Avatar: {avatar_type}, Email flag: {send_email_flag}")

    # Send email if checkbox was ticked
    email_ok = False
    if send_email_flag == "1":
        try:
            if ctx and short_name:
                ensure_data_branch(dbg)
                ctx_push(short_name, ctx, dbg)
            email_ok = send_email(email, company, ctx, dbg)
        except Exception:
            dbg.err(f"Email CRASH:\n{traceback.format_exc()}")
    else:
        dbg.ok("Email checkbox not ticked – skipping email")

    # Call the correct Stage-2 function based on avatar type
    post_ok = False
    try:
        if avatar_type == "type2":
            post_ok = Post_Advanced(ctx, fd, dbg)
        else:
            post_ok = Post_Original(ctx, fd, dbg)
    except Exception:
        dbg.err(f"Post CRASH:\n{traceback.format_exc()}")

    dbg.ok("=== SUMMARY ===")
    if send_email_flag == "1":
        dbg.ok(f"Email: {'OK' if email_ok else 'FAIL'}")
    dbg.ok(f"Post Data ({avatar_type}): {'OK' if post_ok else 'FAIL'}")
    dbg.ok("=== DONE ===")

    return render_template("post_data.html", version=VERSION, debug_log=dbg.text(),
                           company=company, short_name=short_name,
                           avatar_type=avatar_type, email_ok=email_ok,
                           post_ok=post_ok, send_email_flag=send_email_flag)

@app.route("/health")
def health():
    return {"status":"ok","version":VERSION}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
