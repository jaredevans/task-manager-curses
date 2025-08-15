# task-manager-curses

A **text-based task manager** for your console, built with Python’s `curses`/`ncurses`.  
Stores tasks locally in SQLite and can **sync bidirectionally** with Google Tasks.

---

## Features

- **Interactive curses TUI**
  - Add, edit, delete, reorder tasks
  - Toggle tasks as done/undone
  - Color-coded due dates (overdue, soon, etc.)
- **Local Storage**
  - Saves tasks in SQLite database (`tasks.db` by default)
  - Supports task reordering
- **Google Tasks Sync**
  - Push local changes (create/update/delete) to Google Tasks
  - Pull remote changes (including completions) into local DB
  - Conflict resolution by last updated timestamp
- **Offline-First**
  - Works without Google connection; sync later
- **Keyboard Shortcuts**
  - `a` — Add task
  - `d` — Mark done/undone
  - `e` — Edit task
  - `Del` — Delete task
  - `space` — Move task
  - `o` — Order by task or date
  - `g` — Sync with Google Tasks
  - `G` — Run OAuth test
  - `q` — Quit

---

## Installation

```bash
pip install google-auth google-auth-oauthlib google-api-python-client
```

---

## Running

```bash
python tasks.py --db ~/tasks.db
```

First time? Run OAuth flow to get token (after you've set up Google Tasks API below):

```bash
python tasks.py --oauth --client-secret ~/path/to/client_secret.json
```

---

## Using Multiple Google Accounts

You can keep **separate tokens** for personal and work Google accounts so you can switch between them. Note: you can keep the same client_secret.json for both.

- Run OAuth with a specific token path for each account:
- 
  ```bash
  # Personal account
  python tasks.py --oauth \
      --client-secret ~/path/to/client_secret.json \
      --token ~/tasks_personal_token.json

  # Work account
  python tasks.py --oauth \
      --client-secret ~/path/to/client_secret.json \
      --token ~/tasks_work_token.json
  ```

- When running normally, point to the correct token:
- 
  ```bash
  # Use personal account
  python tasks.py --db ~/tasks_personal.db \
      --token ~/tasks_personal_token.json

  # Use work account
  python tasks.py --db ~/tasks_work.db \
      --token ~/tasks_work_token.json
  ```

💡 **Tip:** You can store both personal and work task lists in the same database if you want, but keeping them in separate `.db` files makes it easier to keep them fully independent.

---

## First, the Google Tasks API Setup

### 1) Create/select a Google Cloud project
- Go to [Google Cloud Console](https://console.cloud.google.com/)
- Select an existing project or create a new one.
- Make sure you’re in the right project before continuing.

---

### 2) Enable Google Tasks API
- In the left sidebar: **APIs & Services → Library**
- Search for “Google Tasks API” and click it.
- Click **Enable**.

---

### 3) Configure OAuth Consent Screen
- Go to **APIs & Services → OAuth consent screen**
- **User type:** External (okay even if only you use it)
- Fill out app name and email.
- **Test mode:** Add your own Google account under **Test users**.
- Save.

---

### 4) Create an OAuth Client ID (Desktop App)
- **APIs & Services → Credentials → Create credentials → OAuth client ID**
- Application type: **Desktop app**
- Name it (e.g., `Tasks TUI`) and click **Create**.
- Download the JSON — this is your `client_secret.json`.

---

### 5) Place `client_secret.json`
Options:

- Put it in the same directory as `tasks.py` (rename to `client_secret.json`)

- Or pass path with `--client-secret` (if not renaming):

  ```bash
  python tasks.py --oauth --client-secret ~/Downloads/client_secret_xxx.json
  ```

- Or set an env var:

  ```bash
  export GOOGLE_CLIENT_SECRET=~/Downloads/client_secret_xxx.json
  ```

---

### 6) Run OAuth Flow
Run:

```
python tasks.py --oauth
```

This will:

- Open browser for consent
- Save the token (`token.json` by default, or path from `--token`)
- Rename this token for personal or work

---

## Screenshots

Main view:  
![Main View](https://i.imgur.com/sA1lH9P.png)

Adding new task:  
![Add Task](https://i.imgur.com/6PDsS63.png)

---

## Notes
- If Google Tasks sync fails, see `sync.log` for error details.
- Use `--token` to easily switch between personal and work accounts.
