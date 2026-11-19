CREATE TABLE junban_seat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    active INTEGER NOT NULL,
    created_at TEXT)
;

CREATE TABLE junban_ticket (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    biz_day INTEGER NOT NULL,
    num INTEGER NOT NULL,
    seat_id INTEGER NOT NULL,
    people INTEGER NOT NULL,
    status TEXT NOT NULL,
    token TEXT NOT NULL,
    created_at TEXT,
    called_at TEXT,
    done_at TEXT)
;
