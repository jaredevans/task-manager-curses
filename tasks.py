#!/usr/bin/env python3
import curses
import curses.textpad
import sqlite3
import datetime
import argparse
import os
import sys
import traceback
import time
from typing import Optional, List, Tuple

# ---- Google Tasks deps (installed via pip) ----
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

DB_FILENAME = 'tasks.db'
# Defaults (can be overridden by flags or env)
GOOGLE_TOKEN = os.environ.get('GOOGLE_TOKEN', 'token.json')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', 'client_secret.json')
SCOPES = ['https://www.googleapis.com/auth/tasks']  # read/write
DEBUG_LOG = 'sync.log'

# ------------- Utilities -----------------

def log_exception(e: Exception):
    try:
        with open(DEBUG_LOG, 'a') as f:
            f.write(f"\n[{datetime.datetime.now().isoformat()}] {e}\n")
            f.write(traceback.format_exc())
    except Exception:
        pass  # never let logging crash the app

def normalize_date(date_str):
    date_str = date_str.strip()
    date_str = date_str.replace('.', '/').replace(' ', '/')
    return date_str

def mmdd_to_rfc3339(mmdd: str, year: Optional[int] = None) -> Optional[str]:
    """
    Convert 'MM/DD' to RFC3339 midnight UTC for Google Tasks 'due'.
    If parse fails, return None.
    """
    try:
        if not year:
            year = datetime.date.today().year
        m, d = map(int, mmdd.split('/'))
        dt = datetime.datetime(year, m, d, 0, 0, 0, tzinfo=datetime.timezone.utc)
        return dt.isoformat().replace('+00:00', 'Z')
    except Exception:
        return None

def rfc3339_to_mmdd(rfc: str) -> str:
    """
    Convert Google Tasks 'due' RFC3339 to 'MM/DD' (drop year in local DB semantics).
    """
    try:
        dt = datetime.datetime.fromisoformat(rfc.replace('Z', '+00:00'))
        return f"{dt.month}/{dt.day}"
    except Exception:
        return ""

def notify_popup(stdscr, msg: str, wait_for_key=True):
    """Centered modal message so it won't get overwritten by the main loop."""
    if stdscr is None:
        print(msg)
        if wait_for_key:
            try:
                input("\nPress Enter to continue… ")
            except EOFError:
                pass
        return
    try:
        h, w = stdscr.getmaxyx()
        lines = msg.split('\n')
        box_w = max(min(max(len(s) for s in lines) + 4, w - 4), 20)
        box_h = min(len(lines) + 4, h - 2)
        y = (h - box_h) // 2
        x = (w - box_w) // 2
        win = curses.newwin(box_h, box_w, y, x)
        win.box()
        for i, line in enumerate(lines[:box_h-4]):
            win.addstr(2 + i, 2, line[:box_w-4])
        if wait_for_key:
            footer = "Press any key…"
            win.addstr(box_h - 2, (box_w - len(footer)) // 2, footer)
        win.refresh()
        if wait_for_key:
            win.getch()
    except curses.error:
        _notify(stdscr, msg)
        if wait_for_key:
            stdscr.getch()

def _notify(stdscr, msg: str):
    if stdscr is None:
        print(msg)
        return
    try:
        h, w = stdscr.getmaxyx()
        stdscr.addstr(h-1, 0, " " * (w-1))
        stdscr.addstr(h-1, 0, msg[:w-1])
        stdscr.refresh()
    except curses.error:
        pass

# ------------- DB -----------------

def init_db(db_path: str):
    """
    Initialize the database and create/upgrade the tasks table with extra sync columns.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            pos INTEGER NOT NULL,
            completion_date TEXT NOT NULL,
            details TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0
        )
    ''')
    conn.commit()

    # Migrations: add columns if missing
    def add_col(name, coldef):
        try:
            cur.execute(f'ALTER TABLE tasks ADD COLUMN {name} {coldef}')
            conn.commit()
        except sqlite3.OperationalError:
            pass  # already exists

    add_col('google_id', 'TEXT')   # maps to Google Task id
    add_col('etag', 'TEXT')        # Google ETag for optimistic concurrency
    add_col('updated', 'TEXT')     # Google 'updated' timestamp (RFC3339)
    add_col('dirty', 'INTEGER DEFAULT 0')  # local changes not pushed

    # Ensure an index for google_id lookups
    try:
        cur.execute('CREATE INDEX IF NOT EXISTS idx_tasks_google_id ON tasks(google_id)')
        conn.commit()
    except sqlite3.OperationalError:
        pass

    return conn

def add_task(conn, text, completion_date, details, mark_dirty=True):
    cur = conn.cursor()
    cur.execute('SELECT COALESCE(MAX(pos), -1) FROM tasks')
    max_pos = cur.fetchone()[0]
    new_pos = max_pos + 1
    cur.execute('''
        INSERT INTO tasks (text, pos, completion_date, details, done, dirty)
        VALUES (?, ?, ?, ?, 0, ?)
    ''', (text, new_pos, completion_date, details, 1 if mark_dirty else 0))
    conn.commit()

def delete_task(conn, task_id, mark_dirty=True):
    cur = conn.cursor()
    cur.execute('SELECT google_id FROM tasks WHERE id=?', (task_id,))
    row = cur.fetchone()
    if row and row[0]:
        cur.execute('''CREATE TABLE IF NOT EXISTS deletions (
            google_id TEXT PRIMARY KEY
        )''')
        cur.execute('INSERT OR IGNORE INTO deletions(google_id) VALUES (?)', (row[0],))
        conn.commit()
    cur.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()

def get_tasks(conn, order_by='pos'):
    cur = conn.cursor()
    if order_by == 'pos':
        cur.execute('SELECT id, text, pos, completion_date, details, done FROM tasks ORDER BY pos')
    elif order_by == 'completion_date':
        cur.execute('SELECT id, text, pos, completion_date, details, done FROM tasks ORDER BY completion_date')
    else:
        cur.execute('SELECT id, text, pos, completion_date, details, done FROM tasks ORDER BY pos')
    return cur.fetchall()

def update_task_order(conn, tasks_order):
    cur = conn.cursor()
    for new_pos, task in enumerate(tasks_order):
        task_id = task[0]
        cur.execute('UPDATE tasks SET pos = ?, dirty=1 WHERE id = ?', (new_pos, task_id))
    conn.commit()

def toggle_task_done(conn, task_id, current_done):
    new_done = 0 if current_done else 1
    cur = conn.cursor()
    cur.execute('UPDATE tasks SET done = ?, dirty=1 WHERE id = ?', (new_done, task_id))
    conn.commit()

def update_task_info(conn, task_id, text, completion_date, details):
    cur = conn.cursor()
    cur.execute('''
        UPDATE tasks
        SET text = ?, completion_date = ?, details = ?, dirty = 1
        WHERE id = ?
    ''', (text, completion_date, details, task_id))
    conn.commit()

# ------------- Google OAuth / Service -----------------

def _resolve_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))

def _find_client_secret(path_hint: Optional[str]) -> Optional[str]:
    # Priority: explicit path hint -> env -> cwd -> ~/.config/tasks/client_secret.json
    candidates = []
    if path_hint:
        candidates.append(path_hint)
    if GOOGLE_CLIENT_SECRET and GOOGLE_CLIENT_SECRET not in candidates:
        candidates.append(GOOGLE_CLIENT_SECRET)
    candidates.append('client_secret.json')
    candidates.append(os.path.join(os.path.expanduser('~/.config/tasks'), 'client_secret.json'))
    for p in candidates:
        rp = _resolve_path(p)
        if os.path.exists(rp):
            return rp
    return None

def _find_token_path(path_hint: Optional[str]) -> str:
    if path_hint:
        return _resolve_path(path_hint)
    return _resolve_path(GOOGLE_TOKEN)

def get_google_service(stdscr=None, client_secret_path: Optional[str] = None, token_path: Optional[str] = None):
    """
    Returns a Google Tasks API service.
    - Looks for client_secret.json in several locations or uses --client-secret path.
    - Saves token.json to the chosen token_path (or default).
    """
    client_secret = _find_client_secret(client_secret_path)
    token_file = _find_token_path(token_path)

    if not client_secret:
        notify_popup(stdscr, "Missing client_secret.json\nEnable Tasks API and supply it via:\n"
                             "  - put client_secret.json next to this script, or\n"
                             "  - export GOOGLE_CLIENT_SECRET=~/path/to/client_secret.json, or\n"
                             "  - use --client-secret /path/to/client_secret.json")
        return None

    creds = None
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception as e:
            log_exception(e)
            _notify(stdscr, f"Bad token file: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                _notify(stdscr, "Refreshing Google token…")
                creds.refresh(Request())
            except Exception as e:
                log_exception(e)
                _notify(stdscr, f"Token refresh failed: {e}")
                creds = None

        if not creds:
            try:
                notify_popup(stdscr, "Opening browser for Google OAuth…\nApprove the access.", wait_for_key=False)
                flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
                creds = flow.run_local_server(port=0)
                with open(token_file, 'w') as token:
                    token.write(creds.to_json())
                notify_popup(stdscr, f"OAuth success.\nToken saved to:\n{token_file}")
            except Exception as e:
                log_exception(e)
                notify_popup(stdscr, f"OAuth error:\n{e}\nSee {DEBUG_LOG} for details.")
                return None

    try:
        service = build('tasks', 'v1', credentials=creds)
        return service
    except Exception as e:
        log_exception(e)
        notify_popup(stdscr, f"Failed to build Google Tasks service:\n{e}")
        return None

# ------------- Google Sync Logic -----------------

def ensure_default_tasklist(service):
    # Use the user's default list (usually '@default')
    return '@default'

def push_local_changes(conn, service, stdscr=None):
    """
    Push locally 'dirty' rows to Google (create/update). Push deletions too.
    - When a task is done locally, set status=completed and include a 'completed' timestamp.
    - When a task is not done, set status=needsAction (omit 'completed').
    """
    if service is None:
        return

    tasklist = ensure_default_tasklist(service)
    cur = conn.cursor()

    # Handle deletions first
    cur.execute('''CREATE TABLE IF NOT EXISTS deletions (google_id TEXT PRIMARY KEY)''')
    cur.execute('SELECT google_id FROM deletions')
    to_delete = [row[0] for row in cur.fetchall()]
    for gid in to_delete:
        try:
            service.tasks().delete(tasklist=tasklist, task=gid).execute()
        except Exception:
            pass  # already gone is fine
    if to_delete:
        cur.execute('DELETE FROM deletions')
        conn.commit()

    # Push dirty items (create or update)
    cur.execute('SELECT * FROM tasks WHERE dirty=1 ORDER BY pos')
    rows = cur.fetchall()
    for r in rows:
        rid = r['id']
        payload = {
            'title': r['text'] or '',
            'notes': r['details'] or ''
        }
        due_iso = mmdd_to_rfc3339(r['completion_date'])
        if due_iso:
            payload['due'] = due_iso

        if r['done']:
            payload['status'] = 'completed'
            payload['completed'] = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
        else:
            payload['status'] = 'needsAction'

        try:
            if r['google_id']:
                resp = service.tasks().patch(
                    tasklist=tasklist,
                    task=r['google_id'],
                    body=payload
                ).execute()
            else:
                resp = service.tasks().insert(
                    tasklist=tasklist,
                    body=payload
                ).execute()

            cur.execute('''
                UPDATE tasks SET google_id=?, etag=?, updated=?, dirty=0 WHERE id=?
            ''', (resp.get('id'), resp.get('etag'), resp.get('updated'), rid))
            conn.commit()
        except Exception as e:
            log_exception(e)
            _notify(stdscr, f"Push failed for task {r['text']}: {e}")

def pull_remote_changes(conn, service, stdscr=None):
    """
    Pull from Google and upsert into local DB, resolving conflicts by 'updated' timestamp.
    - Fetches completed and hidden tasks, so server-side completions are reflected locally.
    - Keeps local 'pos' ordering unless the item is new—then append to end.
    """
    if service is None:
        return

    tasklist = ensure_default_tasklist(service)
    cur = conn.cursor()

    # Build mapping: google_id -> local row
    cur.execute('SELECT * FROM tasks')
    local_rows = cur.fetchall()
    local_by_gid = {}
    max_pos = -1
    for r in local_rows:
        max_pos = max(max_pos, r['pos'])
        if r['google_id']:
            local_by_gid[r['google_id']] = r

    # Pull all tasks, including completed & hidden
    page_token = None
    remote_items = []
    while True:
        kwargs = {
            'tasklist': tasklist,
            'showDeleted': False,
            'showCompleted': True,
            'showHidden': True
        }
        if page_token:
            kwargs['pageToken'] = page_token
        resp = service.tasks().list(**kwargs).execute()
        items = resp.get('items', [])
        remote_items.extend(items)
        page_token = resp.get('nextPageToken')
        if not page_token:
            break

    # Upsert / reconcile
    for item in remote_items:
        gid = item.get('id')
        title = item.get('title', '')
        notes = item.get('notes', '') or ''
        due = item.get('due')
        status = item.get('status', 'needsAction')
        updated = item.get('updated')
        etag = item.get('etag')

        mmdd = rfc3339_to_mmdd(due) if due else ''
        done = 1 if status == 'completed' else 0

        if gid in local_by_gid:
            local = local_by_gid[gid]
            local_updated = local['updated']
            local_dirty = local['dirty']

            should_pull = False
            if not local_dirty:
                if not local_updated:
                    should_pull = True
                else:
                    try:
                        ru = datetime.datetime.fromisoformat(updated.replace('Z', '+00:00')) if updated else None
                        lu = datetime.datetime.fromisoformat(local_updated.replace('Z', '+00:00')) if local_updated else None
                        if ru and lu and ru > lu:
                            should_pull = True
                    except Exception:
                        should_pull = True

            if should_pull:
                cur.execute('''
                    UPDATE tasks
                    SET text=?, details=?, completion_date=?, done=?, etag=?, updated=?, dirty=0
                    WHERE id=?
                ''', (title, notes, mmdd, done, etag, updated, local['id']))
                conn.commit()
        else:
            max_pos += 1
            cur.execute('''
                INSERT INTO tasks (text, pos, completion_date, details, done, google_id, etag, updated, dirty)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            ''', (title, max_pos, mmdd, notes, done, gid, etag, updated))
            conn.commit()

# ---- Minimal-move reordering using LIS ---------------------------------------

def _lis_indices(seq: List[int]) -> List[int]:
    """
    Return indices of one Longest Increasing Subsequence in 'seq' (strictly increasing),
    using patience sorting with predecessor reconstruction. O(n log n).
    """
    if not seq:
        return []

    # tails[k] = index in seq of the smallest tail of all increasing subsequences of length k+1
    tails: List[int] = []
    prev: List[Optional[int]] = [None] * len(seq)
    # positions[k] = seq index where LIS of length k+1 ends
    positions: List[int] = []

    from bisect import bisect_left

    for i, x in enumerate(seq):
        # Find insertion point in tails using current values
        vals = [seq[t] for t in tails]
        j = bisect_left(vals, x)
        if j == len(tails):
            tails.append(i)
            positions.append(i)
        else:
            tails[j] = i
            positions[j] = i
        prev[i] = tails[j-1] if j > 0 else None

    # Reconstruct indices from last tail
    lis_end = tails[-1]
    lis_indices = []
    while lis_end is not None:
        lis_indices.append(lis_end)
        lis_end = prev[lis_end]  # type: ignore
    lis_indices.reverse()
    return lis_indices

def _fetch_remote_order_ids(service, tasklist: str, id_filter: set) -> List[str]:
    """Return remote task IDs in their current Google order, filtered to id_filter."""
    ordered = []
    page_token = None
    while True:
        kwargs = dict(tasklist=tasklist, showCompleted=True, showHidden=True, showDeleted=False)
        if page_token:
            kwargs['pageToken'] = page_token
        resp = service.tasks().list(**kwargs).execute()
        for it in resp.get('items', []):
            gid = it.get('id')
            if gid and gid in id_filter:
                ordered.append(gid)
        page_token = resp.get('nextPageToken')
        if not page_token:
            break
    return ordered

def ensure_remote_order_matches_local_min_moves(
    conn,
    service,
    stdscr=None,
    tasklist='@default',
    dry_run=False,
    move_limit=None,
    sleep_between=0.15
):
    """
    Minimize moves by computing an LIS of the remote order mapped into desired local order.
    Only tasks NOT in the LIS are moved. Result exactly matches local 'pos' order.

    Steps:
      1) desired_ids = local Google-linked tasks ordered by local pos.
      2) remote_in_desired = remote order filtered to desired_ids.
      3) Map each remote ID -> desired index; create sequence of desired indices.
      4) Compute LIS of that sequence -> those are already in correct relative order.
      5) Iterate desired_ids top→bottom, moving only IDs not in LIS, placing after last placed.
    """
    if service is None:
        return

    cur = conn.cursor()
    cur.execute('''
        SELECT google_id, text
        FROM tasks
        WHERE google_id IS NOT NULL
        ORDER BY pos ASC
    ''')
    rows = cur.fetchall()
    desired_ids = [r['google_id'] for r in rows]
    if not desired_ids:
        _notify(stdscr, "Order sync: no Google-linked tasks to reorder.")
        return

    desired_set = set(desired_ids)
    remote_in_desired = _fetch_remote_order_ids(service, tasklist, desired_set)

    # Some tasks might be missing remotely; filter desired_ids to those that exist remotely
    remote_set = set(remote_in_desired)
    filtered_desired_ids = [gid for gid in desired_ids if gid in remote_set]
    if not filtered_desired_ids:
        _notify(stdscr, "Order sync: none of the desired tasks exist remotely; skipping.")
        return

    # Map desired_id -> desired index
    desired_index = {gid: i for i, gid in enumerate(filtered_desired_ids)}

    # Build sequence of desired indices in current remote order (only those present in filtered_desired_ids)
    seq = [desired_index[gid] for gid in remote_in_desired if gid in desired_index]

    # Compute LIS over 'seq' -> returns indices into 'seq'; translate back to remote_in_desired gids
    lis_seq_indices = _lis_indices(seq)
    lis_gids = {remote_in_desired[i] for i in lis_seq_indices}

    # Now walk the target desired order; place only items not in LIS.
    moves_done = 0
    anchor = None  # last placed gid in final order
    for gid in filtered_desired_ids:
        if move_limit is not None and moves_done >= move_limit:
            _notify(stdscr, f"Order sync: hit move_limit ({move_limit}); stopping early.")
            break

        # If gid is already in LIS, treat it as placed without moving (just advance anchor)
        if gid in lis_gids:
            anchor = gid
            continue

        try:
            if dry_run:
                if anchor:
                    _notify(stdscr, f"[dry-run] would move {gid} after {anchor}")
                else:
                    _notify(stdscr, f"[dry-run] would move {gid} to top")
            else:
                if anchor:
                    service.tasks().move(tasklist=tasklist, task=gid, previous=anchor).execute()
                else:
                    service.tasks().move(tasklist=tasklist, task=gid).execute()
                moves_done += 1
                if sleep_between:
                    time.sleep(sleep_between)
        except Exception as e:
            log_exception(e)
            # light retry
            try:
                time.sleep(0.6)
                if anchor:
                    service.tasks().move(tasklist=tasklist, task=gid, previous=anchor).execute()
                else:
                    service.tasks().move(tasklist=tasklist, task=gid).execute()
                moves_done += 1
            except Exception as e2:
                log_exception(e2)
                _notify(stdscr, f"Order sync: move failed for {gid}: {e2}")

        anchor = gid

    _notify(stdscr, f"Order sync (min-moves) complete. Moves issued: {moves_done} (kept {len(lis_gids)} in place).")

def full_sync(conn, stdscr=None, client_secret_path: Optional[str] = None, token_path: Optional[str] = None):
    """
    Bi-directional sync: push local dirty first, then pull remote changes,
    then align remote order to match local 'pos' with minimal moves.
    """
    service = get_google_service(stdscr, client_secret_path, token_path)
    if not service:
        _notify(stdscr, "Google service unavailable (OAuth not done?).")
        return
    _notify(stdscr, "Sync: pushing local changes…")
    push_local_changes(conn, service, stdscr)
    _notify(stdscr, "Sync: pulling remote changes…")
    pull_remote_changes(conn, service, stdscr)

    _notify(stdscr, "Sync: aligning Google order (min moves)…")
    ensure_remote_order_matches_local_min_moves(
        conn,
        service,
        stdscr=stdscr,
        tasklist=ensure_default_tasklist(service),
        dry_run=False,          # set True to preview without changes
        move_limit=None,        # cap if you ever need to
        sleep_between=0.12
    )

    _notify(stdscr, "Sync complete.")

# ------------- Curses UI -----------------

def input_task(stdscr, prompt):
    curses.echo()
    stdscr.addstr(curses.LINES - 1, 0, " " * (curses.COLS - 1))
    stdscr.addstr(curses.LINES - 1, 0, prompt)
    stdscr.refresh()
    text = stdscr.getstr(curses.LINES - 1, len(prompt)).decode('utf-8')
    curses.noecho()
    return text

def new_task_dialog(stdscr):
    return dialog_template(stdscr, "", "", "", "New Task")

def edit_task_dialog(stdscr, initial_text, initial_date, initial_details):
    return dialog_template(stdscr, initial_text, initial_date, initial_details, "Edit Task")

def dialog_template(stdscr, init_text, init_date, init_details, title):
    sh, sw = stdscr.getmaxyx()
    dh, dw = 11, 60
    dy, dx = (sh - dh) // 2, (sw - dw) // 2
    win = curses.newwin(dh, dw, dy, dx)
    win.keypad(True)

    fields = [init_text, init_date, init_details]
    prompts = ["Task:", "Completion Date (MM/DD):", "Task Details:"]
    current_field = 0

    while True:
        win.clear()
        win.border()
        win.addstr(0, (dw - len(title)) // 2, title, curses.A_BOLD)
        for i, prompt in enumerate(prompts):
            win.addstr(2 + i*2, 2, prompt)
            content = fields[i]
            field_x = 2 + len(prompt) + 1
            if current_field == i:
                win.addstr(2 + i*2, field_x, content + " " * (dw - field_x - 2 - len(content)), curses.A_REVERSE)
            else:
                win.addstr(2 + i*2, field_x, content)
        btn_y = dh - 3
        ok_label = "[ OK ]"
        cancel_label = "[ CANCEL ]"
        ok_x = dw // 2 - len(ok_label) - 2
        cancel_x = dw // 2 + 2
        win.addstr(btn_y, ok_x, ok_label, curses.A_REVERSE if current_field == 3 else curses.A_NORMAL)
        win.addstr(btn_y, cancel_x, cancel_label, curses.A_REVERSE if current_field == 4 else curses.A_NORMAL)
        win.refresh()

        key = win.getch()
        if key in (9, curses.KEY_DOWN):
            current_field = (current_field + 1) % 5
        elif key == curses.KEY_UP:
            current_field = (current_field - 1) % 5
        elif current_field < 3:
            if key in [curses.KEY_ENTER, 10, 13]:
                current_field = (current_field + 1) % 5
            elif key in [curses.KEY_BACKSPACE, 127, 8]:
                if fields[current_field]:
                    fields[current_field] = fields[current_field][:-1]
            elif 32 <= key <= 126:
                fields[current_field] += chr(key)
        else:
            if key in [curses.KEY_ENTER, 10, 13]:
                if current_field == 3:
                    return tuple(fields)
                else:
                    return None

def main(stdscr, db_path, client_secret_path=None, token_path=None):
    conn = init_db(db_path)
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_CYAN, curses.COLOR_BLACK)
    if curses.can_change_color():
        curses.init_color(8, 300, 300, 300)
        curses.init_pair(6, 8, curses.COLOR_BLACK)
    else:
        curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLACK)

    if not _find_client_secret(client_secret_path):
        _notify(stdscr, "Tip: supply client_secret.json (flag/env/cwd). Press G to test OAuth.")

    current_order = 'pos'
    current_selection = 0
    moving_task_index = None
    reorder_list = None
    scroll_offset = 0

    while True:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()
        visible_tasks = (max_y - 2) // 2
        if visible_tasks < 1:
            stdscr.clear()
            stdscr.addstr(0, 0, "Terminal too small! Please enlarge the window.")
            stdscr.refresh()
            key = stdscr.getch()
            if key == ord('q'):
                break
            continue

        tasks = reorder_list if moving_task_index is not None else get_tasks(conn, current_order)
        num_tasks = len(tasks)
        if current_selection >= num_tasks:
            current_selection = num_tasks - 1
        if current_selection < 0:
            current_selection = 0

        if current_selection < scroll_offset:
            scroll_offset = current_selection
        elif current_selection >= scroll_offset + visible_tasks:
            scroll_offset = current_selection - visible_tasks + 1

        today = datetime.date.today()
        current_year = today.year

        for idx in range(scroll_offset, min(num_tasks, scroll_offset + visible_tasks)):
            task = tasks[idx]
            task_id, text, pos, comp_date, details, done = task
            try:
                month, day = map(int, comp_date.split('/'))
                due_date = datetime.date(current_year, month, day)
            except Exception:
                due_date = today

            delta_days = (due_date - today).days
            if delta_days < 0:
                status = "Overdue"
                status_color = curses.color_pair(1)
            elif delta_days <= 4:
                status = "Needs Attention Now"
                status_color = curses.color_pair(2)
            else:
                status = " "
                status_color = curses.color_pair(3)

            text_part = f"{idx + 1}. {text}"
            details_part = f" | {details} | "
            date_part = f"{comp_date} | "

            if delta_days > 0:
                days_field = f"{delta_days} days left | "
            elif delta_days < 0:
                days_field = f"{abs(delta_days)} days ago | "
            else:
                days_field = "Today | "

            row = (idx - scroll_offset) * 2
            base_date_color = curses.color_pair(1) if (0 <= delta_days <= 4) else curses.color_pair(5)
            task_text_color = curses.color_pair(6) if done else curses.color_pair(4) | curses.A_BOLD

            if moving_task_index is not None and idx == moving_task_index:
                text_style = task_text_color | curses.A_UNDERLINE
                details_style = curses.A_NORMAL
                date_style = base_date_color | curses.A_UNDERLINE
            elif idx == current_selection:
                text_style = task_text_color | curses.A_REVERSE
                details_style = curses.A_REVERSE
                date_style = base_date_color | curses.A_REVERSE
            else:
                text_style = task_text_color
                details_style = curses.A_NORMAL
                date_style = base_date_color

            try:
                stdscr.addstr(row, 0, text_part, text_style)
                stdscr.addstr(row, len(text_part), details_part, details_style)
                stdscr.addstr(row, len(text_part) + len(details_part), date_part, date_style)
                stdscr.addstr(row, len(text_part) + len(details_part) + len(date_part), days_field, date_style)
                stdscr.addnstr(
                    row,
                    len(text_part) + len(details_part) + len(date_part) + len(days_field),
                    status,
                    max_x - (len(text_part) + len(details_part) + len(date_part) + len(days_field)),
                    status_color | curses.A_BOLD
                )
                stdscr.addstr(row + 1, 0, "-" * (max_x - 1))
            except curses.error:
                pass

        instruction = ("a=add, Del=remove, space=move, d=done, e=edit, o=order, "
                       "g=sync, G=OAuth test, q=quit") if moving_task_index is None \
                     else "Moving task. Use arrows to reposition. Space to confirm."
        try:
            stdscr.addstr(max_y - 2, 0, instruction[:max_x-1])
        except curses.error:
            pass

        stdscr.refresh()
        key = stdscr.getch()

        if key == ord('q'):
            break
        elif key == curses.KEY_RESIZE:
            continue
        elif key == curses.KEY_UP:
            if moving_task_index is None:
                current_selection = max(0, current_selection - 1)
            else:
                if moving_task_index > 0:
                    reorder_list[moving_task_index], reorder_list[moving_task_index - 1] = \
                        reorder_list[moving_task_index - 1], reorder_list[moving_task_index]
                    moving_task_index -= 1
                    current_selection = moving_task_index
        elif key == curses.KEY_DOWN:
            if moving_task_index is None:
                current_selection = min(num_tasks - 1, current_selection + 1)
            else:
                if moving_task_index < num_tasks - 1:
                    reorder_list[moving_task_index], reorder_list[moving_task_index + 1] = \
                        reorder_list[moving_task_index + 1], reorder_list[moving_task_index]
                    moving_task_index += 1
                    current_selection = moving_task_index
        elif key == ord('o') and moving_task_index is None:
            order_choice = input_task(stdscr, "Order by (t) task or (d) date? ").strip().lower()
            if order_choice == 't':
                current_order = 'pos'
            elif order_choice == 'd':
                current_order = 'completion_date'
            current_selection = 0
            scroll_offset = 0
        elif key == ord('a') and moving_task_index is None:
            new_task = new_task_dialog(stdscr)
            if new_task is not None:
                task_text, task_date, task_details = new_task
                task_date = normalize_date(task_date)
                add_task(conn, task_text, task_date, task_details, mark_dirty=True)
                tasks = get_tasks(conn, current_order)
                current_selection = len(tasks) - 1
                if current_selection >= scroll_offset + visible_tasks:
                    scroll_offset = current_selection - visible_tasks + 1
        elif key in (curses.KEY_DC, curses.KEY_BACKSPACE, 127) and moving_task_index is None:
            if num_tasks > 0:
                task_id = tasks[current_selection][0]
                delete_task(conn, task_id, mark_dirty=True)
                tasks = get_tasks(conn, current_order)
                if current_selection >= len(tasks):
                    current_selection = max(0, len(tasks) - 1)
                scroll_offset = 0
        elif key == ord('d') and moving_task_index is None:
            if num_tasks > 0:
                task = tasks[current_selection]
                task_id, _, _, _, _, done = task
                toggle_task_done(conn, task_id, done)
        elif key == ord('e') and moving_task_index is None:
            if num_tasks > 0:
                task = tasks[current_selection]
                task_id, text, pos, comp_date, details, done = task
                edited = edit_task_dialog(stdscr, text, comp_date, details)
                if edited is not None:
                    new_text, new_date, new_details = edited
                    new_date = normalize_date(new_date)
                    update_task_info(conn, task_id, new_text, new_date, new_details)
        elif key == ord(' '):
            if current_order != 'pos':
                curses.flash()
            else:
                if moving_task_index is None:
                    reorder_list = get_tasks(conn, current_order)
                    moving_task_index = current_selection
                else:
                    update_task_order(conn, reorder_list)
                    current_selection = moving_task_index
                    moving_task_index = None
                    reorder_list = None
        elif key == ord('g'):  # sync with Google
            try:
                notify_popup(stdscr, "Starting Google sync…\n(May open a browser on first run)", wait_for_key=False)
                full_sync(conn, stdscr, client_secret_path, token_path)
                notify_popup(stdscr, "Sync complete.")
            except Exception as e:
                log_exception(e)
                notify_popup(stdscr, f"Sync error:\n{e}\nSee {DEBUG_LOG}")
        elif key == ord('G'):  # force OAuth/connect test
            svc = get_google_service(stdscr, client_secret_path, token_path)
            if svc:
                try:
                    svc.tasklists().get(tasklist='@default').execute()
                    notify_popup(stdscr, "Google connected.\nTasks API is ready.")
                except Exception as e:
                    log_exception(e)
                    notify_popup(stdscr, f"Connected but API check failed:\n{e}\nSee {DEBUG_LOG}")

    conn.close()

# ------------- Entrypoint -----------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Task Manager using curses (with Google Tasks sync)")
    parser.add_argument('--db', default=DB_FILENAME, help="Path to tasks.db")
    parser.add_argument('--oauth', action='store_true',
                        help="Run Google OAuth flow (saves token.json) and exit (no TUI).")
    parser.add_argument('--client-secret', default=None,
                        help="Path to OAuth client_secret.json (overrides env/cwd).")
    parser.add_argument('--token', default=None,
                        help="Path to token.json to use/save (overrides env/cwd).")
    args = parser.parse_args()
    DB_FILENAME = args.db

    if args.oauth:
        print("Starting Google OAuth for Google Tasks…")
        print("Tip: you can pass --client-secret /path/to/client_secret.json")
        svc = get_google_service(
            stdscr=None,
            client_secret_path=args.client_secret,
            token_path=args.token
        )
        if svc:
            try:
                svc.tasklists().get(tasklist='@default').execute()
                print("✅ OAuth complete and API verified. token saved at:",
                      os.path.abspath(args.token or GOOGLE_TOKEN))
            except Exception as e:
                print(f"⚠️ OAuth likely succeeded (token saved), but API check failed: {e}")
        else:
            print("❌ OAuth failed. Double-check the client secret path and that Tasks API is enabled.")
        sys.exit(0)

    curses.wrapper(lambda s: main(s, DB_FILENAME, args.client_secret, args.token))
