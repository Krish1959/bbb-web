# BBB-Web: LiveAvatar Client Onboarding Automation

Agentic Process Automation — Step 1: Form → Scrape → Generate HeyGen Context → GitHub → Email

---

## What It Does

1. **Serves a form** collecting: Name, Company, Email, Phone, Web URL
2. **Scrapes the company website** — extracts title, description, text, sub-pages, social links, emails
3. **Generates a HeyGen-compatible context file** (`.txt` in Markdown format) with:
   - Contact info, Opening Intro, all sub-page links
   - Full `##PERSONA` prompt section ready for HeyGen avatar
4. **Pushes to GitHub:**
   - `submissions.csv` — appends each form submission (all form data + timestamp)
   - `Context/<company_short_name>.txt` — the generated context file
5. **Sends email** to the client's email address with the full context for human vetting

---

## File Structure

```
bbb-web/
├── app.py              # Main Flask application
├── requirements.txt    # Python dependencies
├── render.yaml         # Render.com deployment config
├── .gitignore
├── README.md
└── templates/
    ├── index.html      # Form UI (dark theme)
    └── success.html    # Confirmation page
```

---

## GitHub Repo Structure (after submissions)

```
Krish1959/bbb-web/
├── (app files above)
├── submissions.csv          # Auto-created: all form submissions
└── Context/
    ├── bescon.txt           # e.g. from www.bescon.sg
    ├── acme.txt             # e.g. from www.acme.com
    └── ...
```

---

## Deployment on Render.com

### Step 1: Push Code to GitHub

```bash
cd bbb-web
git init
git remote add origin https://github.com/Krish1959/bbb-web.git
git add .
git commit -m "Initial: LiveAvatar onboarding app"
git branch -M main
git push -u origin main
```

### Step 2: Create Render Web Service

1. Go to [https://dashboard.render.com](https://dashboard.render.com)
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub repo: `Krish1959/bbb-web`
4. Configure:
   - **Name:** `bbb-web`
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120`

### Step 3: Set Environment Variables

In Render Dashboard → your service → **Environment**:

| Variable | Value | Required |
|----------|-------|----------|
| `GITHUB_TOKEN` | Your GitHub Personal Access Token (with `repo` scope) | ✅ Yes |
| `GITHUB_REPO` | `Krish1959/bbb-web` | ✅ Yes |
| `GITHUB_BRANCH` | `main` | ✅ Yes |
| `SMTP_HOST` | `smtp.gmail.com` | For email |
| `SMTP_PORT` | `587` | For email |
| `SMTP_USER` | Your Gmail address | For email |
| `SMTP_PASS` | Gmail App Password (not regular password) | For email |
| `FROM_EMAIL` | Same as SMTP_USER | For email |
| `APP_TITLE` | `LiveAvatar – Client Onboarding` | Optional |
| `SECRET_KEY` | (auto-generated or set your own) | Optional |

### Creating a GitHub Token

1. Go to GitHub → Settings → Developer settings → **Personal access tokens** → Tokens (classic)
2. Click **Generate new token (classic)**
3. Select scope: **`repo`** (full control of private repositories)
4. Copy the token and paste it as `GITHUB_TOKEN` in Render

### Setting Up Gmail SMTP

1. Enable 2-Factor Authentication on your Google account
2. Go to Google Account → Security → **App passwords**
3. Create an app password for "Mail"
4. Use that password as `SMTP_PASS` (not your regular Gmail password)

---

## How the Naming Works

| Company Name | Web URL | Context File |
|---|---|---|
| BESCON Technology Singapore Pte Ltd | www.bescon.sg | `Context/bescon.txt` |
| Acme Corp | https://www.acme.com | `Context/acme.txt` |
| MyStartup | mystart.io | `Context/mystart.txt` |

The short name is extracted from the URL domain (minus `www.` and TLD).

---

## Local Development

```bash
# Clone
git clone https://github.com/Krish1959/bbb-web.git
cd bbb-web

# Install
pip install -r requirements.txt

# Set env vars
export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"
export GITHUB_REPO="Krish1959/bbb-web"
export SMTP_USER="your@gmail.com"
export SMTP_PASS="xxxx xxxx xxxx xxxx"

# Run
python app.py
# → http://localhost:5000
```

---

## API Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Form UI |
| `/submit` | POST | Process submission |
| `/health` | GET | Health check (for Render) |
