PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS centers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
    commission_bps INTEGER NOT NULL CHECK(commission_bps BETWEEN 0 AND 10000)
);
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
    financial_owner TEXT NOT NULL CHECK(financial_owner IN ('operator','external'))
);
CREATE TABLE IF NOT EXISTS shows (
    id TEXT PRIMARY KEY, center_id TEXT NOT NULL REFERENCES centers(id),
    title TEXT NOT NULL DEFAULT '', starts_at INTEGER NOT NULL,
    sales_close_at INTEGER NOT NULL, unit_price INTEGER NOT NULL CHECK(unit_price>0)
);
CREATE TABLE IF NOT EXISTS seats (
    show_id TEXT NOT NULL REFERENCES shows(id), label TEXT NOT NULL,
    PRIMARY KEY(show_id,label)
);
CREATE TABLE IF NOT EXISTS holds (
    id TEXT PRIMARY KEY, show_id TEXT NOT NULL REFERENCES shows(id),
    channel_id TEXT NOT NULL REFERENCES channels(id), buyer_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','expired','released','sold')),
    expires_at INTEGER NOT NULL, extended INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hold_seats (
    hold_id TEXT NOT NULL REFERENCES holds(id), label TEXT NOT NULL,
    PRIMARY KEY(hold_id,label)
);
CREATE TABLE IF NOT EXISTS claims (
    show_id TEXT NOT NULL, label TEXT NOT NULL,
    hold_id TEXT NOT NULL REFERENCES holds(id),
    PRIMARY KEY(show_id,label),
    FOREIGN KEY(show_id,label) REFERENCES seats(show_id,label)
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY, hold_id TEXT NOT NULL UNIQUE REFERENCES holds(id),
    center_id TEXT NOT NULL REFERENCES centers(id),
    state TEXT NOT NULL CHECK(state IN ('awaiting_payment','completing','review','completed','closed')),
    expected_amount INTEGER NOT NULL CHECK(expected_amount>0),
    commission_bps INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
    provider TEXT NOT NULL, provider_ref TEXT NOT NULL, amount INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('unknown','failed','succeeded')),
    allocated INTEGER NOT NULL DEFAULT 0,
    UNIQUE(provider,provider_ref)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_allocated_payment
    ON payments(order_id) WHERE allocated=1;
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
    hold_id TEXT NOT NULL REFERENCES holds(id), label TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('valid','used','cancelled')),
    price INTEGER NOT NULL, center_share INTEGER NOT NULL,
    used_at INTEGER, used_by TEXT, UNIQUE(hold_id,label)
);
CREATE TABLE IF NOT EXISTS refunds (
    id TEXT PRIMARY KEY, payment_id TEXT NOT NULL REFERENCES payments(id),
    ticket_id TEXT UNIQUE REFERENCES tickets(id),
    amount INTEGER NOT NULL CHECK(amount>0), state TEXT NOT NULL
        CHECK(state IN ('pending','paid')), reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refund_receipts (
    refund_id TEXT PRIMARY KEY REFERENCES refunds(id),
    bank_ref TEXT NOT NULL UNIQUE, paid_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id TEXT PRIMARY KEY, center_id TEXT NOT NULL REFERENCES centers(id),
    source TEXT NOT NULL UNIQUE, amount INTEGER NOT NULL,
    eligible_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settlements (
    id TEXT PRIMARY KEY, center_id TEXT NOT NULL REFERENCES centers(id),
    amount INTEGER NOT NULL CHECK(amount>0), bank_ref TEXT NOT NULL UNIQUE,
    paid_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS review_cases (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
    reason TEXT NOT NULL, owner TEXT NOT NULL, created_at INTEGER NOT NULL,
    due_at INTEGER NOT NULL, resolved_at INTEGER,
    UNIQUE(order_id,reason)
);
CREATE TABLE IF NOT EXISTS operations (
    channel_id TEXT NOT NULL REFERENCES channels(id), op_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL, response TEXT NOT NULL,
    PRIMARY KEY(channel_id,op_key)
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
    action TEXT NOT NULL, target TEXT NOT NULL, created_at INTEGER NOT NULL
);
