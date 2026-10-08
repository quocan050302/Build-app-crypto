import os
import shutil
import sqlite3
import time
from pathlib import Path

def run_migration():
    base_dir = Path(__file__).resolve().parent
    db_path = base_dir / "aurum_desk.db"
    backup_path = base_dir / f"aurum_desk.db.bak.{int(time.time())}"

    print(f"[*] Starting idempotent V4 migration for {db_path}...")
    if db_path.exists():
        shutil.copy2(db_path, backup_path)
        print(f"[+] Created consistent backup at {backup_path}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Enable WAL & busy timeout
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA busy_timeout=5000;")

    # 1. Candles table & unique constraint
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='candles';")
    if cur.fetchone():
        # Check if table already has unique constraint by testing index
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='candles';")
        candles_sql = cur.fetchone()[0]
        if "uq_candle_symbol_tf_ts" not in candles_sql:
            print("[*] Upgrading candles table with unique constraint...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS candles_deduped (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol VARCHAR(20) NOT NULL,
                    timeframe VARCHAR(10) NOT NULL,
                    timestamp BIGINT NOT NULL,
                    open FLOAT NOT NULL,
                    high FLOAT NOT NULL,
                    low FLOAT NOT NULL,
                    close FLOAT NOT NULL,
                    volume FLOAT DEFAULT 0.0,
                    is_closed BOOLEAN DEFAULT 1,
                    CONSTRAINT uq_candle_symbol_tf_ts UNIQUE (symbol, timeframe, timestamp)
                );
            """)
            cur.execute("""
                INSERT OR REPLACE INTO candles_deduped (symbol, timeframe, timestamp, open, high, low, close, volume, is_closed)
                SELECT symbol, timeframe, timestamp, open, high, low, close, volume, is_closed
                FROM candles
                ORDER BY id ASC;
            """)
            cur.execute("DROP TABLE candles;")
            cur.execute("ALTER TABLE candles_deduped RENAME TO candles;")
    else:
        cur.execute("""
            CREATE TABLE candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol VARCHAR(20) NOT NULL,
                timeframe VARCHAR(10) NOT NULL,
                timestamp BIGINT NOT NULL,
                open FLOAT NOT NULL,
                high FLOAT NOT NULL,
                low FLOAT NOT NULL,
                close FLOAT NOT NULL,
                volume FLOAT DEFAULT 0.0,
                is_closed BOOLEAN DEFAULT 1,
                CONSTRAINT uq_candle_symbol_tf_ts UNIQUE (symbol, timeframe, timestamp)
            );
        """)

    cur.execute("CREATE INDEX IF NOT EXISTS ix_candles_symbol ON candles(symbol);")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_candles_timeframe ON candles(timeframe);")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_candles_timestamp ON candles(timestamp);")

    # 2. Paper Orders table & column extensions
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_orders';")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE paper_orders (
                id VARCHAR(36) PRIMARY KEY,
                setup_id VARCHAR(50),
                signal_id VARCHAR(50),
                instrument VARCHAR(20) DEFAULT 'XAUUSDT',
                direction VARCHAR(10) NOT NULL,
                state VARCHAR(20) DEFAULT 'candidate',
                order_type VARCHAR(10) DEFAULT 'MARKET',
                timeframe VARCHAR(10) DEFAULT '15M',
                planned_entry FLOAT NOT NULL,
                actual_entry FLOAT,
                stop_loss FLOAT NOT NULL,
                take_profit FLOAT NOT NULL,
                actual_exit FLOAT,
                quantity FLOAT NOT NULL,
                initial_risk_usdt FLOAT NOT NULL,
                risk_pct FLOAT DEFAULT 0.25,
                gross_rr FLOAT DEFAULT 2.0,
                estimated_net_rr FLOAT DEFAULT 1.9,
                fees_assumption FLOAT DEFAULT 0.10,
                slippage_assumption FLOAT DEFAULT 0.10,
                leverage INTEGER DEFAULT 5,
                margin_mode VARCHAR(20) DEFAULT 'ISOLATED',
                estimated_liquidation FLOAT,
                initial_margin FLOAT,
                realized_pnl_net FLOAT,
                realized_r FLOAT,
                exit_cause VARCHAR(30),
                invalidation_reason TEXT,
                strategy_version VARCHAR(20) DEFAULT '1.0.0',
                created_at BIGINT NOT NULL,
                armed_at BIGINT,
                opened_at BIGINT,
                closed_at BIGINT,
                expires_at BIGINT,
                checklist_snapshot TEXT,
                lessons_retrieved TEXT
            );
        """)
    else:
        # Check and add new V4 columns if missing
        cur.execute("PRAGMA table_info(paper_orders);")
        existing_cols = {row[1] for row in cur.fetchall()}
        new_cols = [
            ("leverage", "INTEGER DEFAULT 5"),
            ("margin_mode", "VARCHAR(20) DEFAULT 'ISOLATED'"),
            ("estimated_liquidation", "FLOAT"),
            ("initial_margin", "FLOAT"),
            ("idempotency_key", "VARCHAR(100)"),
            ("setup_instance_id", "VARCHAR(100)")
        ]
        for col_name, col_def in new_cols:
            if col_name not in existing_cols:
                cur.execute(f"ALTER TABLE paper_orders ADD COLUMN {col_name} {col_def};")
                print(f"[+] Added column {col_name} to paper_orders")

    cur.execute("CREATE INDEX IF NOT EXISTS ix_paper_orders_state ON paper_orders(state);")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_paper_orders_idempotency ON paper_orders(idempotency_key);")

    # 3. Day Audits table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS day_audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_str VARCHAR(10) UNIQUE NOT NULL,
            initial_equity FLOAT DEFAULT 1000.0,
            current_equity FLOAT DEFAULT 1000.0,
            realized_pnl_today FLOAT DEFAULT 0.0,
            fills_count INTEGER DEFAULT 0,
            consecutive_losses INTEGER DEFAULT 0,
            cooldown_until BIGINT,
            is_blocked BOOLEAN DEFAULT 0,
            block_reason VARCHAR(255)
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_day_audits_date_str ON day_audits(date_str);")

    # 4. Economic News & Reactions
    cur.execute("""
        CREATE TABLE IF NOT EXISTS economic_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id VARCHAR(100) UNIQUE,
            title VARCHAR(255) NOT NULL,
            country VARCHAR(10) DEFAULT 'USD',
            currency VARCHAR(10) DEFAULT 'USD',
            impact VARCHAR(20) DEFAULT 'Low',
            scheduled_at BIGINT NOT NULL,
            received_at BIGINT NOT NULL,
            forecast VARCHAR(50),
            previous VARCHAR(50),
            actual VARCHAR(50),
            revised VARCHAR(50),
            raw_json TEXT
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_economic_news_scheduled ON economic_news(scheduled_at);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS news_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER NOT NULL,
            reaction_1m FLOAT,
            reaction_5m FLOAT,
            reaction_15m FLOAT,
            range_usdt FLOAT,
            FOREIGN KEY (news_id) REFERENCES economic_news(id)
        );
    """)

    # 5. Research Reports
    cur.execute("""
        CREATE TABLE IF NOT EXISTS research_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type VARCHAR(30) NOT NULL,
            created_at BIGINT NOT NULL,
            session_name VARCHAR(50) NOT NULL,
            d_4h_bias VARCHAR(50) NOT NULL,
            h1_alignment VARCHAR(50) NOT NULL,
            m15_pois TEXT,
            liquidity_levels TEXT,
            scenarios TEXT,
            content_markdown TEXT NOT NULL,
            strategy_version VARCHAR(20) DEFAULT '1.0.0'
        );
    """)

    # 6. Lessons
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at BIGINT NOT NULL,
            title VARCHAR(255) NOT NULL,
            category VARCHAR(50) DEFAULT 'PROCESS',
            related_trade_id VARCHAR(36),
            setup_type VARCHAR(50),
            session VARCHAR(50),
            reflection TEXT NOT NULL,
            action_rule TEXT NOT NULL,
            is_hard_filter BOOLEAN DEFAULT 0,
            is_approved BOOLEAN DEFAULT 1
        );
    """)

    # 7. System Configs (KV settings)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_configs (
            "key" VARCHAR(50) NOT NULL PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at BIGINT NOT NULL
        );
    """)

    # 8. Watch Setups (Upcoming Plans & Setups state machine)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS watch_setups (
            id VARCHAR(50) NOT NULL PRIMARY KEY,
            version INTEGER DEFAULT 1,
            strategy VARCHAR(50) DEFAULT 'SMC_V1',
            direction VARCHAR(10) NOT NULL,
            timeframe VARCHAR(10) DEFAULT '15M',
            state VARCHAR(30) DEFAULT 'WATCHING',
            htf_bias VARCHAR(20) DEFAULT 'UNKNOWN',
            h1_alignment VARCHAR(20) DEFAULT 'UNKNOWN',
            poi_zone TEXT,
            trigger_mode VARCHAR(30) DEFAULT 'CONFIRMED_CLOSE',
            provisional_entry FLOAT NOT NULL,
            provisional_sl FLOAT NOT NULL,
            provisional_tp FLOAT NOT NULL,
            confirmed_entry FLOAT,
            confirmed_sl FLOAT,
            confirmed_tp FLOAT,
            invalidation_price FLOAT NOT NULL,
            invalidation_reason TEXT,
            gross_rr FLOAT DEFAULT 0.0,
            net_rr FLOAT DEFAULT 0.0,
            risk_usdt FLOAT DEFAULT 0.0,
            quantity FLOAT DEFAULT 0.0,
            leverage INTEGER DEFAULT 5,
            margin_mode VARCHAR(20) DEFAULT 'ISOLATED',
            estimated_liquidation FLOAT,
            conditions_met TEXT,
            conditions_remaining TEXT,
            distance_to_entry_atr FLOAT,
            distance_to_entry_usdt FLOAT,
            news_window TEXT,
            evidence_timeline TEXT,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            expires_at BIGINT
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_watch_setups_state ON watch_setups(state);")

    # Helper for idempotent column addition
    def add_column_if_missing(table: str, col_name: str, col_def: str):
        cur.execute(f"PRAGMA table_info({table});")
        cols = [r[1] for r in cur.fetchall()]
        if col_name not in cols:
            print(f"[*] Adding column {col_name} ({col_def}) to {table}...")
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def};")

    # Watch setups extensions
    add_column_if_missing("watch_setups", "setup_instance_id", "VARCHAR(100)")
    add_column_if_missing("watch_setups", "near_entry_alerted_at", "BIGINT")
    add_column_if_missing("watch_setups", "near_entry_distance_price", "FLOAT")
    add_column_if_missing("watch_setups", "near_entry_distance_atr", "FLOAT")
    add_column_if_missing("watch_setups", "entry_zone_low", "FLOAT")
    add_column_if_missing("watch_setups", "entry_zone_high", "FLOAT")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_watch_setups_instance ON watch_setups(setup_instance_id);")

    # 9. Domain Events (Event sourcing stream for WebSockets & audit)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS domain_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id VARCHAR(36) NOT NULL UNIQUE,
            sequence INTEGER NOT NULL,
            schema_version VARCHAR(20) DEFAULT '1.0.0',
            event_type VARCHAR(50) NOT NULL,
            aggregate_id VARCHAR(50) NOT NULL,
            aggregate_version INTEGER DEFAULT 1,
            occurred_at BIGINT NOT NULL,
            published_at BIGINT NOT NULL,
            payload TEXT NOT NULL
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_domain_events_type ON domain_events(event_type);")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_domain_events_agg ON domain_events(aggregate_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_domain_events_seq ON domain_events(sequence);")

    # 10. Notification Outbox (Reliable delivery & retry queue)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notification_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id VARCHAR(36),
            channel VARCHAR(20) DEFAULT 'TELEGRAM',
            recipient VARCHAR(100),
            message_type VARCHAR(50) NOT NULL,
            dedupe_key VARCHAR(120) NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            status VARCHAR(20) DEFAULT 'PENDING',
            priority VARCHAR(20) DEFAULT 'STANDARD',
            attempts INTEGER DEFAULT 0,
            last_attempt_at BIGINT,
            next_attempt_at BIGINT,
            occurred_at BIGINT,
            provider_message_id VARCHAR(100),
            error_message TEXT,
            created_at BIGINT NOT NULL
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_notification_outbox_status ON notification_outbox(status);")

    # Notification outbox extensions
    add_column_if_missing("notification_outbox", "priority", "VARCHAR(20) DEFAULT 'STANDARD'")
    add_column_if_missing("notification_outbox", "next_attempt_at", "BIGINT")
    add_column_if_missing("notification_outbox", "occurred_at", "BIGINT")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_notification_outbox_priority ON notification_outbox(priority);")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_notification_outbox_next_attempt ON notification_outbox(next_attempt_at);")

    # 11. Telegram Configs
    cur.execute("""
        CREATE TABLE IF NOT EXISTS telegram_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enabled BOOLEAN DEFAULT 0,
            bot_token VARCHAR(150),
            chat_id VARCHAR(100),
            subscribed_events TEXT,
            quiet_hours_enabled BOOLEAN DEFAULT 0,
            quiet_hours_start VARCHAR(10) DEFAULT '23:00',
            quiet_hours_end VARCHAR(10) DEFAULT '06:00',
            bypass_critical_quiet_hours BOOLEAN DEFAULT 1,
            near_entry_mode VARCHAR(20) DEFAULT 'ATR',
            near_entry_atr_mult FLOAT DEFAULT 0.5,
            near_entry_price_dist FLOAT DEFAULT 2.0,
            near_entry_cooldown_min INTEGER DEFAULT 30,
            timezone VARCHAR(50) DEFAULT 'Asia/Ho_Chi_Minh',
            base_chart_url VARCHAR(255),
            updated_at BIGINT NOT NULL
        );
    """)

    # Telegram configs extensions
    add_column_if_missing("telegram_configs", "bypass_critical_quiet_hours", "BOOLEAN DEFAULT 1")
    add_column_if_missing("telegram_configs", "near_entry_mode", "VARCHAR(20) DEFAULT 'ATR'")
    add_column_if_missing("telegram_configs", "near_entry_atr_mult", "FLOAT DEFAULT 0.5")
    add_column_if_missing("telegram_configs", "near_entry_price_dist", "FLOAT DEFAULT 2.0")
    add_column_if_missing("telegram_configs", "near_entry_cooldown_min", "INTEGER DEFAULT 30")

    # Expand legacy subscriptions in telegram_configs
    import json
    cur.execute("SELECT id, subscribed_events FROM telegram_configs;")
    for row in cur.fetchall():
        cfg_id, sub_json = row[0], row[1]
        if sub_json:
            try:
                subs = json.loads(sub_json)
                updated = False
                # If legacy ARMED_NEAR_ENTRY is present, expand to ARMED and NEAR_ENTRY
                if "ARMED_NEAR_ENTRY" in subs:
                    if "ARMED" not in subs:
                        subs.append("ARMED")
                        updated = True
                    if "NEAR_ENTRY" not in subs:
                        subs.append("NEAR_ENTRY")
                        updated = True
                # If legacy CLOSED is present, expand to TP_HIT, SL_HIT, MANUAL_CLOSED
                if "CLOSED" in subs:
                    for ev in ["TP_HIT", "SL_HIT", "MANUAL_CLOSED"]:
                        if ev not in subs:
                            subs.append(ev)
                            updated = True
                # Ensure REJECTED is included if subscribed to orders
                if "ARMED" in subs and "REJECTED" not in subs:
                    subs.append("REJECTED")
                    updated = True
                if updated:
                    cur.execute("UPDATE telegram_configs SET subscribed_events = ? WHERE id = ?;", (json.dumps(subs), cfg_id))
            except Exception:
                pass

    # 12. Replay Runs & Replay Trades
    cur.execute("""
        CREATE TABLE IF NOT EXISTS replay_runs (
            id VARCHAR(36) PRIMARY KEY,
            run_name VARCHAR(100) NOT NULL,
            symbol VARCHAR(20) DEFAULT 'XAUUSDT',
            start_ts BIGINT NOT NULL,
            end_ts BIGINT NOT NULL,
            initial_equity FLOAT DEFAULT 1000.0,
            final_equity FLOAT NOT NULL,
            total_trades INTEGER DEFAULT 0,
            net_wins INTEGER DEFAULT 0,
            net_losses INTEGER DEFAULT 0,
            breakevens INTEGER DEFAULT 0,
            win_rate FLOAT DEFAULT 0.0,
            profit_factor FLOAT DEFAULT 0.0,
            max_drawdown FLOAT DEFAULT 0.0,
            expectancy_r FLOAT DEFAULT 0.0,
            config_snapshot TEXT NOT NULL,
            rvol_ablation_summary TEXT,
            created_at BIGINT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS replay_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id VARCHAR(36) NOT NULL,
            symbol VARCHAR(20) DEFAULT 'XAUUSDT',
            direction VARCHAR(10) NOT NULL,
            entry_time BIGINT NOT NULL,
            exit_time BIGINT NOT NULL,
            entry_price FLOAT NOT NULL,
            exit_price FLOAT NOT NULL,
            stop_loss FLOAT NOT NULL,
            take_profit FLOAT NOT NULL,
            quantity FLOAT NOT NULL,
            gross_pnl FLOAT NOT NULL,
            net_pnl FLOAT NOT NULL,
            net_r FLOAT NOT NULL,
            exit_cause VARCHAR(50) NOT NULL,
            rvol_at_entry FLOAT,
            FOREIGN KEY (run_id) REFERENCES replay_runs (id) ON DELETE CASCADE
        );
    """)

    # Seed default system configs if missing
    now_ms = int(time.time() * 1000)
    defaults = [
        ("schema_version", "4.0.0"),
        ("auto_paper_trading", "false"), # Default false as per prompt section 8
        ("default_leverage", "5"),
        ("default_margin_mode", "ISOLATED"),
        ("active_strategy_version", "1.0.0"),
    ]
    for key, val in defaults:
        cur.execute("SELECT 1 FROM system_configs WHERE key = ?", (key,))
        if not cur.fetchone():
            cur.execute("INSERT INTO system_configs (key, value, updated_at) VALUES (?, ?, ?)", (key, val, now_ms))

    # Seed Telegram config if missing
    cur.execute("SELECT count(*) FROM telegram_configs;")
    if cur.fetchone()[0] == 0:
        import json
        cur.execute("""
            INSERT INTO telegram_configs (enabled, bot_token, chat_id, subscribed_events, quiet_hours_enabled, quiet_hours_start, quiet_hours_end, timezone, base_chart_url, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            0, "", "",
            json.dumps(["READY", "ARMED_NEAR_ENTRY", "FILLED", "INVALIDATED", "CLOSED", "FEED_DOWN"]),
            0, "23:00", "06:00", "Asia/Ho_Chi_Minh", "", now_ms
        ))

    # Seed initial rules/lessons if empty
    cur.execute("SELECT count(*) FROM lessons;")
    if cur.fetchone()[0] == 0:
        initial_lessons = [
            (now_ms, "Không giao dịch trong vùng tin tức USD High Impact (Blackout)", "RISK", None, "ALL", "ALL",
             "Biến động tin tức quét hai đầu wick mạnh (slippage cao), không phản ánh cấu trúc thanh khoản thông thường.",
             "Chặn mở lệnh 30 phút trước và 15 phút sau tin USD High Impact (FOMC: 60m trước / 30m sau).", 1, 1),
            (now_ms, "Bắt buộc có Liquidity Sweep trước khi vào lệnh CHoCH/BOS", "RULE_COMPLIANCE", None, "SWEEP_FVG", "ALL",
             "Vào lệnh khi chưa có Liquidity Sweep rất dễ thành mục tiêu bị quét của phiên tiếp theo.",
             "Chỉ kích hoạt Candidate khi có nến wick quét qua Swing High/Low đã xác nhận rồi đóng ngược lại.", 1, 1),
            (now_ms, "Tỷ lệ Net R:R tối thiểu 1:2.0 sau khi trừ chi phí", "RISK", None, "ALL", "ALL",
             "Các lệnh dưới 2R không bù đắp được rủi ro trượt giá và phí giao dịch trong dài hạn.",
             "Từ chối mọi tín hiệu có R:R kỳ vọng < 2.0. Không kéo dãn TP giả tạo để đạt R:R.", 1, 1),
            (now_ms, "Dừng giao dịch ngày sau 2 lệnh lỗ liên tiếp", "PROCESS", None, "ALL", "ALL",
             "Giao dịch sau chuỗi thua thường bị ảnh hưởng tâm lý gỡ gạc (revenge trading).",
             "Khóa mở lệnh mới trong ngày nếu đã chịu 2 lần stop-loss liên tiếp.", 1, 1)
        ]
        cur.executemany("""
            INSERT INTO lessons (created_at, title, category, related_trade_id, setup_type, session, reflection, action_rule, is_hard_filter, is_approved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, initial_lessons)

    # ==================== V5.1 SCHEMA ADDITIONS & DATA REMEDIATION ====================
    # 1. EconomicNews URL & research fields
    cur.execute("PRAGMA table_info(economic_news);")
    existing_news_cols = [c[1] for c in cur.fetchall()]
    new_news_cols = [
        ("source_url", "VARCHAR(500)"),
        ("event_type_id", "VARCHAR(100)"),
        ("source_timezone", "VARCHAR(50) DEFAULT 'America/New_York'"),
        ("source_time_raw", "VARCHAR(100)"),
        ("schedule_kind", "VARCHAR(20) DEFAULT 'EXACT'"),
        ("gold_relevance", "VARCHAR(20) DEFAULT 'LOW'"),
        ("research_status", "VARCHAR(30) DEFAULT 'NOT_FETCHED'"),
        ("research_assessment", "TEXT"),
        ("research_fetched_at", "BIGINT"),
    ]
    for col_name, col_type in new_news_cols:
        if col_name not in existing_news_cols:
            print(f"[*] Adding column {col_name} to economic_news...")
            cur.execute(f"ALTER TABLE economic_news ADD COLUMN {col_name} {col_type};")

    # 2. Normalize legacy 24:00 in telegram_configs
    cur.execute("SELECT id, quiet_hours_start, quiet_hours_end FROM telegram_configs;")
    for row in cur.fetchall():
        cfg_id, q_start, q_end = row
        changed = False
        new_start = q_start
        new_end = q_end
        if q_start in ("24:00", "24:0"):
            new_start = "00:00"
            changed = True
        if q_end in ("24:00", "24:0"):
            new_end = "00:00"
            changed = True
        if changed:
            print(f"[*] Normalizing legacy 24:00 in telegram_config id={cfg_id} to 00:00...")
            cur.execute("UPDATE telegram_configs SET quiet_hours_start = ?, quiet_hours_end = ? WHERE id = ?", (new_start, new_end, cfg_id))

    # 3. Remediate legacy armed orders with invalid geometry
    cur.execute("SELECT id, direction, planned_entry, stop_loss, take_profit FROM paper_orders WHERE state = 'armed';")
    for row in cur.fetchall():
        ord_id, direction, entry, sl, tp = row
        is_invalid = False
        if direction == "LONG" and not (sl < entry < tp):
            is_invalid = True
        elif direction == "SHORT" and not (tp < entry < sl):
            is_invalid = True
        if is_invalid:
            print(f"[!] Remediating legacy armed order #{ord_id} with invalid {direction} geometry (entry={entry}, sl={sl}, tp={tp}) -> REJECTED")
            cur.execute("""
                UPDATE paper_orders
                SET state = 'rejected', exit_cause = 'INVALID_PRICE_GEOMETRY', closed_at = ?
                WHERE id = ?;
            """, (now_ms, ord_id))

    conn.commit()
    conn.close()
    print("[+] V5.1 migration completed successfully with full historical preservation!")

if __name__ == "__main__":
    run_migration()
