import curses
import curses.textpad
import sqlite3
import datetime
import argparse

DB_FILENAME = '/Users/jared.evans/python_projs/tasks/tasks.db'

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
    and 'details' columns.
    """
    conn = sqlite3.connect(DB_FILENAME)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            pos INTEGER NOT NULL,
            completion_date TEXT NOT NULL,
            details TEXT NOT NULL
        )
    ''')
    conn.commit()
    return conn

def add_task(conn, text, completion_date, details):
    """
    Insert a new task into the database.
    The new task is appended at the end (largest 'pos' value).
    """
    cur = conn.cursor()
    cur.execute('SELECT MAX(pos) FROM tasks')
    max_pos = cur.fetchone()[0]
    new_pos = 0 if max_pos is None else max_pos + 1
    cur.execute('INSERT INTO tasks (text, pos, completion_date, details) VALUES (?, ?, ?, ?)',
                (text, new_pos, completion_date, details))
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
    Returns a list of tuples: (id, text, pos, completion_date, details).
    """
    cur = conn.cursor()
    if order_by == 'pos':
        cur.execute('SELECT id, text, pos, completion_date, details FROM tasks ORDER BY pos')
    elif order_by == 'completion_date':
        cur.execute('SELECT id, text, pos, completion_date, details FROM tasks ORDER BY completion_date')
    else:
        cur.execute('SELECT id, text, pos, completion_date, details FROM tasks ORDER BY pos')
    return cur.fetchall()

def update_task_order(conn, tasks_order):
    """
    Given a list of tasks (each a tuple of (id, text, pos, completion_date, details))
    in the new order, update the 'pos' values in the database.
    """
    cur = conn.cursor()
    for new_pos, task in enumerate(tasks_order):
        task_id = task[0]
        cur.execute('UPDATE tasks SET pos = ? WHERE id = ?', (new_pos, task_id))
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

def main(stdscr):
    conn = init_db()
    curses.curs_set(0)  # hide the cursor

    # Enable color support and define our color pairs.
    curses.start_color()
    curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)      # Overdue and bright red for due soon
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)   # Due Soon (status text)
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)    # Due Later (status text)
    curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLACK)    # Bright white for task text
    curses.init_pair(5, curses.COLOR_CYAN, curses.COLOR_BLACK)     # Bright cyan for completion date

    # current_order is either 'pos' (default) or 'completion_date'
    current_order = 'pos'
    current_selection = 0  # index of highlighted task
    moving_task_index = None
    reorder_list = None

    # scroll_offset indicates the index of the first task being rendered.
    scroll_offset = 0
    while True:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()

        # Reserve 2 rows for instructions (or warning message)
        visible_tasks = (max_y - 2) // 2
        if visible_tasks < 1:
            stdscr.clear()
            stdscr.addstr(0, 0, "Terminal too small! Please enlarge the window.")
            stdscr.refresh()
            key = stdscr.getch()
            if key == ord('q'):
                break
            continue

        # When not in move mode, fetch tasks using the current_order criteria.
        if moving_task_index is None:
            tasks = get_tasks(conn, current_order)
        else:
            tasks = reorder_list
        num_tasks = len(tasks)
        # Ensure current_selection is within bounds.
        if current_selection >= num_tasks:
            current_selection = num_tasks - 1
        if current_selection < 0:
            current_selection = 0

        # Adjust scroll_offset so the current selection is visible.
        if current_selection < scroll_offset:
            scroll_offset = current_selection
        elif current_selection >= scroll_offset + visible_tasks:
            scroll_offset = current_selection - visible_tasks + 1

        today = datetime.date.today()
        current_year = today.year

        # Render only the visible subset of tasks.
        for idx in range(scroll_offset, min(num_tasks, scroll_offset + visible_tasks)):
            task = tasks[idx]
            task_id, text, pos, comp_date, details = task

            # Determine due status based on the completion date.
            try:
                month, day = map(int, comp_date.split('/'))
                due_date = datetime.date(current_year, month, day)
            except Exception:
                due_date = today  # fallback if parsing fails

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

            # Split the task line into three parts:
            # - text_part: task number and text (bright white, bold)
            # - details_part: details (regular)
            # - date_part: completion date (bright cyan normally, but bright red if "Needs Attention Now")
            text_part = f"{idx + 1}. {text}"
            details_part = f" | {details} | "
            date_part = f"{comp_date} | "

            # Calculate the row relative to the visible window.
            row = (idx - scroll_offset) * 2

            # Choose the base date color:
            # If the task is "Needs Attention Now" (i.e. delta_days between 0 and 4), use bright red.
            if 0 <= delta_days <= 4:
                base_date_color = curses.color_pair(1)  # bright red
            else:
                base_date_color = curses.color_pair(5)  # bright cyan

            # Determine styles.
            if moving_task_index is not None and idx == moving_task_index:
                text_style = curses.color_pair(4) | curses.A_BOLD | curses.A_UNDERLINE
                details_style = curses.A_NORMAL
                date_style = base_date_color | curses.A_BOLD | curses.A_UNDERLINE
            elif idx == current_selection:
                text_style = curses.color_pair(4) | curses.A_REVERSE | curses.A_BOLD
                details_style = curses.A_REVERSE
                date_style = base_date_color | curses.A_REVERSE | curses.A_BOLD
            else:
                text_style = curses.color_pair(4) | curses.A_BOLD
                details_style = curses.A_NORMAL
                date_style = base_date_color | curses.A_BOLD

            try:
                stdscr.addstr(row, 0, text_part, text_style)
                stdscr.addstr(row, len(text_part), details_part, details_style)
                stdscr.addstr(row, len(text_part) + len(details_part), date_part, date_style)
                stdscr.addnstr(row, len(text_part) + len(details_part) + len(date_part), status,
                               max_x - (len(text_part) + len(details_part) + len(date_part)),
                               status_color | curses.A_BOLD)
                stdscr.addstr(row + 1, 0, "-" * (max_x - 1))
            except curses.error:
                pass

        # Display instructions at the bottom.
        if moving_task_index is None:
            instruction = ("Press 'a' to add, 'Del' to remove, space to move, "
                           "'o' to change ordering, 'q' to quit. Use up/down to scroll.")
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
            stdscr.clear()
            task_text = input_task(stdscr, "Enter new task: ")
            task_date = input_task(stdscr, "Enter completion date (MM/DD): ")
            task_date = normalize_date(task_date)
            task_details = input_task(stdscr, "Enter details: ")
            if task_text.strip() and task_date.strip() and task_details.strip():
                add_task(conn, task_text, task_date, task_details)
                tasks = get_tasks(conn, current_order)
                current_selection = len(tasks) - 1
                if current_selection >= scroll_offset + visible_tasks:
                    scroll_offset = current_selection - visible_tasks + 1
        elif key == curses.KEY_DC and moving_task_index is None:
            if num_tasks > 0:
                task_id = tasks[current_selection][0]
                delete_task(conn, task_id)
                tasks = get_tasks(conn, current_order)
                if current_selection >= len(tasks):
                    current_selection = max(0, len(tasks) - 1)
                scroll_offset = 0
        elif key == ord(' '):
            if current_order != 'pos':
                curses.flash()  # reordering is allowed only when ordering by 'pos'
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
    DB_FILENAME = args.db  # Update the global DB_FILENAME with the provided argument.
    curses.wrapper(main)
