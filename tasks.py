import curses
import curses.textpad
import sqlite3
import datetime
import argparse

DB_FILENAME = 'tasks.db'

def normalize_date(date_str):
    """
    Normalize the date input.
    Converts inputs like "5.21" or "5 21" to "5/21".
    """
    date_str = date_str.strip()
    date_str = date_str.replace('.', '/').replace(' ', '/')
    return date_str

def init_db():
    """
    Initialize the database and create the tasks table with 'pos', 'completion_date',
    'details', and 'done' columns.
    """
    conn = sqlite3.connect(DB_FILENAME)
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
    return conn

def add_task(conn, text, completion_date, details):
    """
    Insert a new task into the database.
    The new task is appended at the end (largest 'pos' value) and marked as not done.
    """
    cur = conn.cursor()
    cur.execute('SELECT MAX(pos) FROM tasks')
    max_pos = cur.fetchone()[0]
    new_pos = 0 if max_pos is None else max_pos + 1
    cur.execute('''
        INSERT INTO tasks (text, pos, completion_date, details, done)
        VALUES (?, ?, ?, ?, 0)
    ''', (text, new_pos, completion_date, details))
    conn.commit()

def delete_task(conn, task_id):
    """
    Delete a task by its ID.
    """
    cur = conn.cursor()
    cur.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()

def get_tasks(conn, order_by='pos'):
    """
    Retrieve all tasks from the database.
    When order_by is 'pos' they are sorted by the pos column;
    when 'completion_date', sorted by the completion_date column.
    Returns a list of tuples: (id, text, pos, completion_date, details, done).
    """
    cur = conn.cursor()
    if order_by == 'pos':
        cur.execute('SELECT id, text, pos, completion_date, details, done FROM tasks ORDER BY pos')
    elif order_by == 'completion_date':
        cur.execute('SELECT id, text, pos, completion_date, details, done FROM tasks ORDER BY completion_date')
    else:
        cur.execute('SELECT id, text, pos, completion_date, details, done FROM tasks ORDER BY pos')
    return cur.fetchall()

def update_task_order(conn, tasks_order):
    """
    Given a list of tasks (each a tuple of (id, text, pos, completion_date, details, done))
    in the new order, update the 'pos' values in the database.
    """
    cur = conn.cursor()
    for new_pos, task in enumerate(tasks_order):
        task_id = task[0]
        cur.execute('UPDATE tasks SET pos = ? WHERE id = ?', (new_pos, task_id))
    conn.commit()

def toggle_task_done(conn, task_id, current_done):
    """
    Toggle the 'done' status of a task.
    """
    new_done = 0 if current_done else 1
    cur = conn.cursor()
    cur.execute('UPDATE tasks SET done = ? WHERE id = ?', (new_done, task_id))
    conn.commit()

def update_task_info(conn, task_id, text, completion_date, details):
    """
    Update the text, completion_date, and details of a task.
    """
    cur = conn.cursor()
    cur.execute('''
        UPDATE tasks 
        SET text = ?, completion_date = ?, details = ?
        WHERE id = ?
    ''', (text, completion_date, details, task_id))
    conn.commit()

def input_task(stdscr, prompt):
    """
    Display a prompt and allow the user to enter a line of text.
    """
    curses.echo()
    stdscr.addstr(curses.LINES - 1, 0, prompt)
    stdscr.clrtoeol()
    text = stdscr.getstr(curses.LINES - 1, len(prompt)).decode('utf-8')
    curses.noecho()
    return text

def new_task_dialog(stdscr):
    """
    Display a text-based dialog box for creating a new task.
    Allows the user to fill in three fields (task, completion date, task details)
    and then select one of two buttons: OK or CANCEL.
    The user uses TAB (or up/down arrow keys) to move between fields.
    ENTER on OK returns a tuple (task, completion_date, details);
    ENTER on CANCEL returns None.
    """
    return dialog_template(stdscr, "", "", "", "New Task")

def edit_task_dialog(stdscr, initial_text, initial_date, initial_details):
    """
    Display a dialog box for editing an existing task.
    The fields are pre-filled with initial_text, initial_date, and initial_details.
    Returns a tuple (task, completion_date, details) if OK is selected,
    or None if CANCEL is chosen.
    """
    return dialog_template(stdscr, initial_text, initial_date, initial_details, "Edit Task")

def dialog_template(stdscr, init_text, init_date, init_details, title):
    """
    Generic dialog box template used for both new and edit dialogs.
    It displays three fields and two buttons.
    Returns a tuple (text, date, details) if OK is selected, else None.
    """
    sh, sw = stdscr.getmaxyx()
    dh, dw = 11, 60  # increased height to show title and more space
    dy, dx = (sh - dh) // 2, (sw - dw) // 2
    win = curses.newwin(dh, dw, dy, dx)
    win.keypad(True)

    # Pre-populate the fields
    fields = [init_text, init_date, init_details]
    prompts = ["Task:", "Completion Date (MM/DD):", "Task Details:"]
    current_field = 0  # cycles through 0,1,2,3,4 where 3=OK, 4=CANCEL

    while True:
        win.clear()
        win.border()
        # Title centered at the top
        win.addstr(0, (dw - len(title)) // 2, title, curses.A_BOLD)
        # Display the three fields
        for i, prompt in enumerate(prompts):
            win.addstr(2 + i*2, 2, prompt)
            content = fields[i]
            field_x = 2 + len(prompt) + 1
            if current_field == i:
                win.addstr(2 + i*2, field_x, content + " " * (dw - field_x - 2 - len(content)), curses.A_REVERSE)
            else:
                win.addstr(2 + i*2, field_x, content)
        # Display buttons on the bottom row (position dh-2)
        btn_y = dh - 3
        ok_label = "[ OK ]"
        cancel_label = "[ CANCEL ]"
        ok_x = dw // 2 - len(ok_label) - 2
        cancel_x = dw // 2 + 2
        if current_field == 3:
            win.addstr(btn_y, ok_x, ok_label, curses.A_REVERSE)
        else:
            win.addstr(btn_y, ok_x, ok_label)
        if current_field == 4:
            win.addstr(btn_y, cancel_x, cancel_label, curses.A_REVERSE)
        else:
            win.addstr(btn_y, cancel_x, cancel_label)
        win.refresh()

        key = win.getch()
        if key == 9:  # TAB
            current_field = (current_field + 1) % 5
        elif key in [curses.KEY_DOWN]:
            current_field = (current_field + 1) % 5
        elif key in [curses.KEY_UP]:
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
                if current_field == 3:  # OK
                    return tuple(fields)
                else:  # CANCEL
                    return None

def main(stdscr):
    conn = init_db()
    curses.curs_set(0)  # hide the cursor

    # Enable color support and define our color pairs.
    curses.start_color()
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)      # Overdue and due soon (red)
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)   # Due soon (yellow)
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)    # Due later (green)
    curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLACK)    # Bright white for task text
    curses.init_pair(5, curses.COLOR_CYAN, curses.COLOR_BLACK)     # Bright cyan for completion date

    # Define a custom dark grey color (if supported).
    if curses.can_change_color():
        curses.init_color(8, 300, 300, 300)  # dark grey
        curses.init_pair(6, 8, curses.COLOR_BLACK)
    else:
        curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLACK)

    # current_order is either 'pos' (default) or 'completion_date'
    current_order = 'pos'
    current_selection = 0  # index of highlighted task
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

        if moving_task_index is None:
            tasks = get_tasks(conn, current_order)
        else:
            tasks = reorder_list
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
            if done:
                task_text_color = curses.color_pair(6)
            else:
                task_text_color = curses.color_pair(4) | curses.A_BOLD

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

        if moving_task_index is None:
            instruction = ("Press 'a' to add, 'Del' to remove, space to move, "
                           "'d' to toggle done, 'e' to edit, 'o' to change ordering, 'q' to quit. Use up/down to scroll.")
        else:
            instruction = "Moving task. Use arrow keys to reposition. Press space to confirm new order."
        try:
            stdscr.addstr(max_y - 2, 0, instruction)
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
            prompt = "Order tasks by (t) tasks or (d) date? "
            order_choice = input_task(stdscr, prompt).strip().lower()
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
                add_task(conn, task_text, task_date, task_details)
                tasks = get_tasks(conn, current_order)
                current_selection = len(tasks) - 1
                if current_selection >= scroll_offset + visible_tasks:
                    scroll_offset = current_selection - visible_tasks + 1
        elif key in (curses.KEY_DC, curses.KEY_BACKSPACE, 127) and moving_task_index is None:
            if num_tasks > 0:
                task_id = tasks[current_selection][0]
                delete_task(conn, task_id)
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
                # Retrieve current task values
                task = tasks[current_selection]
                task_id, text, pos, comp_date, details, done = task
                # Open edit dialog pre-filled with current values.
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

    conn.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Task Manager using curses")
    parser.add_argument('--db', default=DB_FILENAME, help="Path to tasks.db")
    args = parser.parse_args()
    DB_FILENAME = args.db
    curses.wrapper(main)
