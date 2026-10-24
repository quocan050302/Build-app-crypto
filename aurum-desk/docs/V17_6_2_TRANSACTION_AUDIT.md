# KIỂM TOÁN TÍNH AN TOÀN GIAO DỊCH VÀ QUYỀN SỞ HỮU SESSION (TRANSACTION SAFETY AUDIT)
## AURUM DESK — PHIÊN BẢN V17.6.2

---

### 1. BỐI CẢNH & PHẠM VI KIỂM TOÁN (AUDIT SCOPE & BACKGROUND)

Trong các hệ thống giao dịch định lượng thời gian thực (Quantitative Algorithmic Trading Systems), việc phân tách ranh giới giao dịch (transaction boundary isolation) giữa luồng thực thi lệnh (Order Execution Pipeline) và luồng đo lường quan sát (Observability / Telemetry Subsystem) là yêu cầu tối quan trọng cấp P0.

Trước phiên bản V17.6.2, một khiếm khuyết nghiêm trọng (**FIX-01**) đã được phát hiện trong `backend/services/engine_observability_service.py`:
- Khi engine chính (`scalp_watcher_orchestrator.py`) gọi các hàm ghi nhận telemetry (như `record_engine_heartbeat`, `record_engine_incident`, `resolve_engine_incident`) và truyền vào đối tượng `db: Session` mượn từ caller context, các hàm này đã tự ý thực hiện `target_db.commit()`.
- **Nguy cơ tiềm tàng**: Nếu caller đang thực hiện một chuỗi tính toán hoặc thay đổi trạng thái (chẳng hạn cập nhật trạng thái cơ hội SMC/ICT, phân bổ rủi ro risk budget, ghi nhận order payload), việc telemetry commit giữa chừng sẽ làm commit sớm các mutation chưa sẵn sàng, phá vỡ tính nguyên tử (Atomicity), và tước đoạt quyền rollback của caller khi xảy ra lỗi sau đó.

Báo cáo kiểm toán này xác nhận ranh giới sở hữu session đã được tái cấu trúc triệt để và bảo đảm an toàn 100% trong V17.6.2.

---

### 2. NGUYÊN TẮC QUYỀN SỞ HỮU SESSION (SESSION OWNERSHIP INVARIANTS)

Kiến trúc V17.6.2 thiết lập quy tắc phân định rõ ràng giữa **Borrowed Session** và **Owned Session**:

```
+-----------------------------------------------------------------------------------+
|                        SESSION OWNERSHIP DECISION MATRIX                          |
+------------------------------------+----------------------------------------------+
| BORROWED SESSION                   | OWNED SESSION                                |
| (Caller cung cấp session: db is not None) | (Caller không cung cấp: db is None)        |
+------------------------------------+----------------------------------------------+
| 1. KHÔNG BAO GIỜ gọi db.commit()   | 1. Khởi tạo session mới qua SessionLocal()   |
| 2. KHÔNG BAO GIỜ gọi db.rollback() | 2. Thực hiện db.commit() khi hoàn tất        |
| 3. KHÔNG BAO GIỜ gọi db.close()    | 3. Thực hiện db.rollback() khi gặp Exception  |
| 4. CHỈ ĐƯỢC PHÉP gọi db.flush()     | 4. Luôn đảm bảo db.close() trong finally     |
|    để gán ID và ràng buộc khóa phụ |                                              |
+------------------------------------+----------------------------------------------+
```

---

### 3. KIỂM KÊ CÁC ĐIỂM THAY ĐỔI TRONG MÃ NGUỒN

Tất cả các phương thức ghi nhận telemetry trong `EngineObservabilityService` đã được chuẩn hóa:

| Phương thức | Trạng thái Session | Hành vi Borrowed (`close_session=False`) | Hành vi Owned (`close_session=True`) |
|:---|:---:|:---|:---|
| `record_engine_heartbeat` | Hỗ trợ cả 2 | `target_db.flush()` | `target_db.commit()`, `target_db.close()` |
| `persist_engine_heartbeat` | Owned Only | N/A (luôn tự mở session mới) | Khởi tạo session riêng, commit và đóng |
| `record_engine_incident` | Hỗ trợ cả 2 | `target_db.flush()` | `target_db.commit()`, `target_db.close()` |
| `persist_engine_incident` | Owned Only | N/A (luôn tự mở session mới) | Khởi tạo session riêng, commit và đóng |
| `resolve_engine_incident` | Hỗ trợ cả 2 | `target_db.flush()` | `target_db.commit()`, `target_db.close()` |
| `resolve_active_incidents`| Hỗ trợ cả 2 | `target_db.flush()` | `target_db.commit()`, `target_db.close()` |
| `record_evaluation_cycle` | Hỗ trợ cả 2 | `target_db.flush()` | `target_db.commit()`, `target_db.close()` |

#### Mã nguồn chuẩn hóa mẫu:
```python
# Trích đoạn chuẩn hóa từ EngineObservabilityService
close_session = False
if db is None:
    target_db = SessionLocal()
    close_session = True
else:
    target_db = db

try:
    # ... thực hiện mutations trên target_db ...
    if close_session:
        target_db.commit()
    else:
        target_db.flush()
except Exception as e:
    if close_session:
        target_db.rollback()
    raise
finally:
    if close_session:
        target_db.close()
```

---

### 4. BẰNG CHỨNG THỰC NGHIỆM TỰ ĐỘNG (AUTOMATED PROOF)

Bộ kiểm thử `backend/tests/test_v17_6_2_transaction_ownership.py` gồm 4 bài kiểm tra độc lập chứng minh tính toàn vẹn:

1. **`test_tx01_borrowed_session_does_not_commit_unrelated_mutations`**:
   - Caller tạo một bản ghi `OpportunityCandidate` nhưng chưa commit.
   - Caller gọi `record_engine_heartbeat(db=caller_session)`.
   - Caller kiểm tra trong database: bản ghi của caller chưa hề được commit.
   - Caller thực hiện `caller_session.rollback()`.
   - Xác minh: Cả bản ghi của caller lẫn heartbeat chưa commit đều bị rollback sạch sẽ, không có rò rỉ mutation. Kết quả: **PASS**.

2. **`test_tx02_incident_writer_does_not_commit_borrowed_session`**:
   - Caller tạo bản ghi chưa commit và gọi `record_engine_incident(db=caller_session)`.
   - Caller thực hiện rollback.
   - Xác minh: Không có dữ liệu nào bị commit ngoài ý muốn. Kết quả: **PASS**.

3. **`test_tx03_incident_resolver_does_not_commit_borrowed_session`**:
   - Caller mở một incident trước đó, sau đó tạo một mutation chưa commit và gọi `resolve_engine_incident(db=caller_session)`.
   - Caller rollback.
   - Xác minh: Ranh giới giao dịch được bảo vệ trọn vẹn. Kết quả: **PASS**.

4. **`test_tx04_owned_session_commits_when_db_is_none`**:
   - Caller gọi `record_engine_heartbeat(db=None)`.
   - Telemetry tự quản lý session, commit thành công và đóng session.
   - Session mới đọc lại từ DB thấy dữ liệu heartbeat đã được lưu bền vững. Kết quả: **PASS**.

---

### 5. KẾT LUẬN & CAM KẾT VẬN HÀNH

- **Bảo toàn chiến lược SMC/ICT**: Không có bất kỳ dòng mã nào can thiệp hay thay đổi logic giao dịch, cấu trúc quét thanh khoản, tỷ lệ R:R hay cơ chế Paper Execution.
- **Không có rò rỉ giao dịch**: Đảm bảo toàn vẹn dữ liệu kế toán và phân bổ vốn trong môi trường Paper Trading.
- **Sẵn sàng triển khai**: Khẳng định đạt 100% tiêu chuẩn nghiệm thu giao dịch an toàn V17.6.2.
