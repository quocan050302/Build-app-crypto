# KIỂM TOÁN TÍNH PHÂN LẬP GIAO DỊCH TELEMETRY & TRANSACTION BOUNDARIES
## AURUM DESK — PHIÊN BẢN V17.6.4 (Phase M, PART 161)

---

### 1. NGUYÊN TẮC THIẾT KẾ CỐT LÕI (CORE ARCHITECTURAL PRINCIPLE)

Trong các hệ thống giao dịch tự động cấp doanh nghiệp, dữ liệu viễn trắc (telemetry - bao gồm heartbeat, chu kỳ đánh giá, và khoảng quan sát) và dữ liệu nghiệp vụ (business transactions - bao gồm lệnh đặt, vị thế, số dư tài khoản) phải tuân thủ nguyên tắc **Phân Lập Giao Dịch Tuyệt Đối (Strict Transaction Isolation)**:

1. **Không làm ô nhiễm giao dịch nghiệp vụ (Zero Business Pollution)**: Việc ghi nhận viễn trắc không được phép thực hiện `commit` lên session của nghiệp vụ, làm vô tình lưu các thay đổi dang dở (uncommitted mutations).
2. **Khả năng sống sót khi giao dịch nghiệp vụ Rollback (Survives Business Rollback)**: Khi một chu kỳ hoặc một giao dịch nghiệp vụ gặp lỗi và phải `rollback()`, dữ liệu bằng chứng viễn trắc (chu kỳ lỗi, heartbeat, sự cố) PHẢI VẪN TỒN TẠI bền vững trong CSDL để phục vụ kiểm toán và chẩn đoán.
3. **Không rò rỉ cơ sở dữ liệu (Zero Database Leakage)**: Khi chạy trong môi trường kiểm thử với SQLite `:memory:` hoặc CSDL tạm, viễn trắc phải gắn kết (bind) chính xác vào cùng engine/kết nối của caller thông qua `db.get_bind()`, cấm tuyệt đối việc tự ý mở `aurum_desk.db` trên đĩa cứng gây ô nhiễm môi trường thật.
4. **Không làm sập luồng nghiệp vụ khi viễn trắc lỗi (Fail-Safe Telemetry)**: Mọi lỗi xảy ra trong quá trình ghi viễn trắc phải được bắt giữ và xử lý nội bộ, không được quăng exception làm gián đoạn luồng đặt lệnh hoặc quản lý vị thế.

---

### 2. CƠ CHẾ TRIỂN KHAI KỸ THUẬT (TECHNICAL IMPLEMENTATION)

#### 2.1. Khởi tạo Session Viễn Trắc Độc Lập qua `_resolve_target_session`
Trong `backend/services/engine_observability_service.py` và `backend/services/observation_interval_service.py`:

```python
@staticmethod
def _resolve_target_session(db: Optional[Session]) -> Tuple[Session, bool]:
    """
    Tạo một phiên SQLAlchemy độc lập được bind trực tiếp vào engine của caller.
    Bảo đảm:
    - Nếu caller dùng SQLite in-memory với StaticPool, session mới vẫn thấy chung schema và bảng.
    - Session mới có transaction context riêng biệt, không commit session của caller.
    """
    if db is not None:
        bind = db.get_bind()
        session_factory = sessionmaker(bind=bind, autoflush=False, autocommit=False)
        return session_factory(), True
    return SessionLocal(), True
```

#### 2.2. Cơ chế Thử Lại khi Bị Khóa Tạm Thời (Transient SQLite Lock Retry)
Đối với SQLite, khi nhiều tiến trình/thread cùng ghi đồng thời, hiện tượng `sqlite3.OperationalError: database is locked` có thể phát sinh. Hệ thống triển khai vòng lặp thử lại có giới hạn với exponential backoff:

```python
for attempt in range(max_retries):
    try:
        session.add(record)
        session.commit()
        break
    except OperationalError as oe:
        session.rollback()
        if "locked" in str(oe).lower() and attempt < max_retries - 1:
            time.sleep(0.05 * (2 ** attempt))
            continue
        raise
```

---

### 3. KIỂM CHỨNG CÁC KỊCH BẢN BIÊN (EDGE CASE VERIFICATION)

```
+-------------------------------------------------------------------------------+
|                             CALLER SESSION (db)                              |
|                                                                               |
|  1. Thêm bản ghi nghiệp vụ (uncommitted): models.SystemConfig(...)            |
|  2. Gọi viễn trắc: engine_observability_service.record_engine_heartbeat(db)     |
|                                                                               |
|                      |  Tách session: bind = db.get_bind()                    |
|                      v                                                        |
|     +--------------------------------------------------+                      |
|     |            ISOLATED TELEMETRY SESSION            |                      |
|     |  - Ghi EngineHeartbeatRecord                     |                      |
|     |  - session.commit() [CHỈ COMMIT VIỄN TRẮC]        |                      |
|     |  - session.close()                               |                      |
|     +--------------------------------------------------+                      |
|                                                                               |
|  3. Caller thực hiện db.rollback()                                            |
|     => Bản ghi nghiệp vụ SystemConfig bị hủy bỏ hoàn toàn                     |
|     => Bản ghi EngineHeartbeatRecord VẪN CÒN NGUYÊN trong DB                  |
+-------------------------------------------------------------------------------+
```

---

### 4. MA TRẬN BẰNG CHỨNG KIỂM THỬ (AUDIT EVIDENCE MATRIX)

| Test ID | Nội Dung Kiểm Toán | Tiêu Chuẩn Đáp Ứng | Bộ Test | Kết Quả |
|:---|:---|:---|:---|:---:|
| **DB01** | Khởi tạo session độc lập qua `db.get_bind()` | Không phụ thuộc SessionLocal mặc định | `test_v17_6_4_telemetry_database_isolation.py` | **PASS** |
| **DB02** | Cô lập heartbeat khi caller rollback | Heartbeat tồn tại sau `caller.rollback()` | `test_v17_6_4_telemetry_database_isolation.py` | **PASS** |
| **DB03** | Cô lập chu kỳ đánh giá khi caller rollback | Evaluation cycle tồn tại sau `caller.rollback()` | `test_v17_6_4_telemetry_database_isolation.py` | **PASS** |
| **DB04** | Cô lập khoảng quan sát khi caller rollback | Observation interval tồn tại sau `caller.rollback()` | `test_v17_6_4_telemetry_database_isolation.py` | **PASS** |
| **DB05** | Không commit đột kích thay đổi của caller | Uncommitted business mutations không bị commit | `test_v17_6_4_telemetry_database_isolation.py` | **PASS** |
| **DB06** | Hoạt động chính xác trên SQLite `:memory:` | Gắn kết đúng `StaticPool` của caller | `test_v17_6_4_telemetry_database_isolation.py` | **PASS** |
| **DB07** | Bền vững sau khi caller `close()` | Phiên viễn trắc hoàn tất tự chủ | `test_v17_6_4_telemetry_database_isolation.py` | **PASS** |
| **DB08** | Khả năng retry khi SQLite transient lock | Tự động phục hồi và commit thành công | `test_v17_6_4_telemetry_database_isolation.py` | **PASS** |
| **DB09** | Viễn trắc thất bại không làm hỏng caller | Exception an toàn, fail-safe | `test_v17_6_4_telemetry_database_isolation.py` | **PASS** |
