import os
import shutil
import sqlite3
import time
from pathlib import Path

def run_migration():
    base_dir = Path(__file__).resolve().parent
    db_path = base_dir / "aurum_desk.db"
    backup_path = base_dir / f"aurum_desk.db.bak.{int(time.time())}"

    print(f"[*] Starting migration for {db_path}...")
    if db_path.exists():
        shutil.copy2(db_path, backup_path)
        print(f"[+] Created backup at {backup_path}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Enable WAL
    cur.execute("PRAGMA journal_mode=WAL;")

    # 1. Deduplicate candles table
    # Check if candles table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='candles';")
    if cur.fetchone():
        print("[*] Deduplicating existing candles...")
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

        # Insert latest entry for each (symbol, timeframe, timestamp)
        cur.execute("""
            INSERT OR REPLACE INTO candles_deduped (symbol, timeframe, timestamp, open, high, low, close, volume, is_closed)
            SELECT symbol, timeframe, timestamp, open, high, low, close, volume, is_closed
            FROM candles
            ORDER BY id ASC;
        """)

        cur.execute("DROP TABLE candles;")
        cur.execute("ALTER TABLE candles_deduped RENAME TO candles;")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_candles_symbol ON candles(symbol);")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_candles_timeframe ON candles(timeframe);")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_candles_timestamp ON candles(timestamp);")
        print("[+] Candles table deduplicated with unique constraint successfully.")
    else:
        # Create fresh candles table
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

    # 2. Create paper_orders table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS paper_orders (
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
            risk_pct FLOAT DEFAULT 0.5,
            gross_rr FLOAT DEFAULT 2.0,
            estimated_net_rr FLOAT DEFAULT 1.9,
            fees_assumption FLOAT DEFAULT 0.10,
            slippage_assumption FLOAT DEFAULT 0.10,
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
    cur.execute("CREATE INDEX IF NOT EXISTS ix_paper_orders_state ON paper_orders(state);")

    # 3. Create day_audits table
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

    # 4. Create economic_news table
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

    # 5. Create news_reactions table
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

    # 6. Create research_reports table
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

    # 7. Create lessons table
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

    # Seed initial rules/lessons if empty
    cur.execute("SELECT count(*) FROM lessons;")
    if cur.fetchone()[0] == 0:
        now_ms = int(time.time() * 1000)
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
        print("[+] Seeded foundational lessons & hard filter rules.")

    conn.commit()
    conn.close()
    print("[+] Migration completed successfully!")

if __name__ == "__main__":
    run_migration()
