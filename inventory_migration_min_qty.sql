-- Consumable Inventory: per-item minimum quantity (0 = no alert) and the
-- comma-separated emails that receive the minimum-quantity alert (blank = default list).
-- Safe to run more than once. The API also runs this on first use if its DB user is allowed to.
ALTER TABLE inventory.consumable_items
    ADD COLUMN IF NOT EXISTS min_qty INTEGER NOT NULL DEFAULT 0 CHECK (min_qty >= 0);
ALTER TABLE inventory.consumable_items
    ADD COLUMN IF NOT EXISTS notify_emails TEXT;
