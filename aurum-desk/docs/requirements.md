# MASTER PROMPT — LOCAL XAUUSDT RESEARCH & AUTO PAPER TRADING APP

Bạn là senior full-stack engineer, kỹ sư dữ liệu thị trường và kỹ sư kiểm chứng hệ thống giao dịch. Hãy xây một ứng dụng local hoàn chỉnh tên **AURUM DESK** cho một người Việt Nam hiểu kiến thức crypto căn bản, chỉ tập trung giao dịch vàng **XAUUSDT trên Bitget**.

Tôi muốn một app làm việc thực tế, với chart nến và tương tác gợi nhớ TradingView, tự nghiên cứu chuyên sâu mỗi ngày và ba phiên Á–Âu–Mỹ; tự đưa ra tín hiệu Entry/SL/TP khi đủ điều kiện; mô phỏng giao dịch; lưu lịch sử và đọc lại bài học để hỗ trợ những lệnh tiếp theo.

Hãy thực hiện xây dựng, chạy và kiểm tra ứng dụng trong workspace này. Đừng dừng ở bản kế hoạch, giao diện mockup hoặc danh sách gợi ý. Thực hiện các lựa chọn kỹ thuật hợp lý, chỉ hỏi khi thiếu thông tin thực sự chặn công việc. Tuân thủ các giới hạn quyền của môi trường Antigravity. Không tự đăng ký dịch vụ trả phí, tự lấy khóa tài khoản hay thực hiện giao dịch tiền thật.

## 1. Phạm vi và mặc định quan trọng

- App chạy trên máy cá nhân, ưu tiên Windows, truy cập qua localhost. Không yêu cầu cloud deployment hoặc tài khoản TradingView.
- Giao diện và giải thích bằng tiếng Việt. Thuật ngữ SMC/ICT phải có tooltip dễ hiểu.
- Instrument duy nhất cho engine giao dịch: `XAUUSDT`, Bitget USDT-margined perpetual futures. Không thay bằng XAUUSD, PAXGUSDT hay BTCUSDT khi nguồn lỗi. XAUUSDT là phái sinh tham chiếu vàng, không phải vàng vật chất.
- Phương pháp: SMC/ICT được chuyển thành bộ quy tắc rõ ràng, có phiên bản và kiểm chứng; không giả vờ quan sát được lệnh của tổ chức hoặc nguyên nhân thị trường từ một mẫu nến.
- Khung bắt buộc: **D, 4H, 1H, 15M, 5M, 1M**. D là daily; 1M trên UI là một phút, không phải một tháng. Trong code dùng `1m` cho một phút.
- Mục tiêu người dùng là khoảng **2–3 cơ hội có chất lượng/ngày**. Đây không phải chỉ tiêu bắt buộc. **Tối đa 3 lần mở lệnh/ngày; ngày không đủ điều kiện có thể 0 lệnh.** Không hạ chuẩn để đủ số lệnh.
- Auto signal: bật mặc định. Engine tự phát tín hiệu hoàn chỉnh khi đủ điều kiện, không yêu cầu người dùng tự bấm “phân tích”.
- Auto paper trading: có công tắc và nút Start/Pause, bật mặc định khi người dùng Start với tài khoản giả lập. Vốn giả lập ban đầu 1.000 USDT. Lệnh là mô phỏng local, không phải lệnh gửi tới Bitget.
- Tuyệt đối không triển khai live execution trong bản này. Chỉ tạo interface riêng để có thể bổ sung sau. UI luôn ghi rõ `PAPER TRADING — VỐN GIẢ LẬP`.
- Không cam kết lợi nhuận, xác suất thắng hay “AI chính xác”. Phải phân biệt điểm chất lượng điều kiện, độ đầy đủ dữ liệu và xác suất thắng đã được hiệu chỉnh. Chưa có hiệu chỉnh thì không hiển thị phần trăm xác suất thắng.

## 2. Công nghệ và cấu trúc

Sử dụng một cấu trúc gọn, dễ bảo trì:

- Frontend: React + TypeScript + Vite.
- Financial chart: TradingView **Lightweight Charts** theo phiên bản được khóa và tài liệu tương ứng. Đây là thư viện chart, không tự cung cấp dữ liệu hoặc toàn bộ tính năng TradingView. Giữ attribution và license theo yêu cầu thư viện.
- Backend: Python + FastAPI; WebSocket từ backend tới frontend.
- Backend lấy dữ liệu Bitget qua adapter REST/WebSocket riêng, theo tài liệu chính thức hiện hành. Không hard-code protocol chưa kiểm tra.
- SQLite cho dữ liệu lâu dài, migration có phiên bản, WAL nếu phù hợp. Không dùng localStorage làm nguồn lưu lịch sử lệnh/bài học.
- SQLAlchemy/Alembic hoặc công cụ tương đương, Decimal cho tiền/giá/khối lượng; serialize an toàn.
- Scheduler local: APScheduler hoặc giải pháp tương đương hỗ trợ IANA timezone và xử lý DST.
- pytest cho logic quan trọng; test frontend và end-to-end cho luồng chính.
- Tạo scripts cài đặt/chạy local, `.env.example`, lockfiles, README tiếng Việt. Có script PowerShell cho Windows; có thể thêm script shell.

Tách các module: market_data, candle_store, sessions, smc_engine, news_provider, news_import, research_reports, signal_engine, risk_engine, paper_broker, journal, lessons, backtest, scheduler, api, frontend.

LLM là tùy chọn qua adapter, mặc định tắt nếu chưa có API key. Khi không có LLM, app vẫn chạy đủ bằng engine deterministic và báo cáo có template. Không yêu cầu LLM để vẽ chart hoặc tính rủi ro. Nếu bổ sung LLM: nó chỉ giải thích dữ liệu đã xác minh và tổng hợp bài học, không tạo giá/số liệu/tin giả, không tự đổi quy tắc giao dịch hoặc gọi broker. Không nhúng secrets vào frontend.

## 3. Dữ liệu giá thật và chất lượng dữ liệu

- Đọc tài liệu Bitget chính thức, xác minh XAUUSDT đang được hỗ trợ và trạng thái hợp đồng trước khi bật engine.
- Lấy contract size/unit, tick size, bước quantity, min quantity, min notional, fee rates, giới hạn và lịch funding từ nguồn hiện hành. Không lấy công thức lot XAUUSD của broker áp cho XAUUSDT.
- REST để bootstrap/backfill nến và lấy cấu hình; WebSocket cho ticker/bid/ask và cập nhật nến nếu supported. Khi WS mất, fallback REST có rate limiting, timeout, exponential backoff và jitter.
- Hiển thị last, bid, ask, mark, index, funding nếu có, cùng timestamp và nguồn.
- Chuẩn hóa timestamp UTC; UI UTC+7. Daily boundary phải cấu hình rõ và cố định trong backtest. Không trộn nến daily có ranh giới khác nhau.
- Kiểm tra nến trùng, đảo thứ tự, missing intervals, OHLC không hợp lệ, clock drift, gap bất thường và giá cũ. Không tự lấp dữ liệu mất bằng giá giả rồi coi là dữ liệu thật.
- Tín hiệu dùng nến đã đóng. Nến đang chạy có thể cập nhật chart nhưng không tính như xác nhận cấu trúc.
- Mục tiêu dữ liệu lịch sử: đủ warm-up cho mỗi khung, cộng dữ liệu 1m để nghiên cứu; khả năng tải bao nhiêu phải theo giới hạn thực tế của sàn. Hiển thị khoảng dữ liệu đã có, không hứa dữ liệu từ trước ngày niêm yết.
- Dữ liệu feed quá hạn, gap ảnh hưởng điều kiện hoặc không xác minh quy cách: chặn tín hiệu executable và mở lệnh mới. Journal/bài học vẫn đọc được khi nguồn giá lỗi.
- Cấu hình freshness riêng cho ticker/nến/news. Nếu dùng polling chậm, hiển thị độ trễ thực; không gọi đó là tick realtime.

## 4. Chart giống trải nghiệm TradingView

Làm một trading workspace, không phải landing page:

- Dark theme charcoal, đường lưới nhẹ, nến xanh/đỏ, vàng làm accent; chữ dễ đọc; responsive desktop/mobile.
- Chart chiếm vùng chính; toolbar D/4H/1H/15M/5M/1M; zoom, pan, crosshair, price/time axes, OHLC, volume, go-to-realtime, fullscreen.
- Hỗ trợ layout một chart và nhiều chart đồng bộ crosshair/thời gian nếu thư viện hỗ trợ; ưu tiên hoàn thiện một chart trước.
- Các lớp bật/tắt: swing high/low, BOS, CHoCH, sweep, FVG, OB tham khảo, premium/discount, PDH/PDL, session high/low, vùng vào, Entry, SL, TP1/TP2 và sự kiện tin tức.
- Vẽ event markers đúng thời gian công bố; hover xem nguồn, giờ, impact, forecast/actual/previous nếu có, thời điểm app nhận dữ liệu.
- Vẽ giao dịch thực sự từ paper journal. Không vẽ giao dịch giả vào lịch sử như đã xảy ra.
- Tooltips giải thích vì sao đánh dấu; màu riêng cho vùng kế hoạch và vị thế đang mở.
- Dụng cụ cơ bản: horizontal line và rectangle, lưu annotations nếu làm. Nếu cần primitive/plugin cho drawing thì triển khai đúng API, không giả định Lightweight Charts có sẵn mọi drawing tool.
- Có loading, disconnected, reconnecting, stale, empty và error states.
- Các panel: trạng thái ba phiên; daily research; checklist tín hiệu; risk; news; open position; nhật ký; lessons; kiến thức; settings/system health.

## 5. Phiên và lịch nghiên cứu sâu

Ba cửa sổ quan sát theo giờ địa phương, điều chỉnh được:

- Asia/Tokyo: 09:00–18:00.
- Europe/London: 08:00–17:00.
- America/New_York: 08:00–17:00.

Đây là cửa sổ nghiên cứu, không phải giờ hợp đồng Bitget hoặc ICT kill zones. Tạo kill zones riêng, có định nghĩa và timezone cấu hình; không coi là bằng chứng xác suất thắng. Phiên London/New York xử lý DST bằng IANA timezone, không offset cố định. Phiên có thể chồng lấn.

Lịch chạy:

1. Báo cáo ngày lúc 06:30 UTC+7; nếu dữ liệu chưa đủ thì trạng thái pending và retry có giới hạn.
2. Báo cáo đầu mỗi phiên, theo timezone phiên.
3. Cập nhật khi nến 15M đóng; kiểm tra execution conditions khi nến 5M/1M đóng; position management theo feed giá.
4. Báo cáo sau mỗi lệnh đóng.
5. Tổng kết ngày lúc 23:30 UTC+7, ghi rõ vị thế còn mở và phiên chưa kết thúc. Không giả vờ đã biết kết quả của phần ngày tương lai.

Báo cáo phải sâu, có bằng chứng:

- Data coverage, timestamp và trạng thái nguồn.
- D/4H: xu hướng/cấu trúc, biên độ, vùng premium/discount, swing chính, ATR và volatility regime được định nghĩa cụ thể.
- 1H: cấu trúc trung gian và mức đồng thuận/xung đột với D/4H.
- 15M: vùng FVG/OB còn hiệu lực, thanh khoản và các vùng chờ.
- 5M/1M: trigger đã xuất hiện hoặc chưa; không dùng làm lý do ghi đè bối cảnh lớn.
- PDH/PDL, session highs/lows; Asia range và phản ứng London/New York khi đã có dữ liệu.
- Tin sắp tới, blackout windows, thông tin chưa có, spread/funding/ký quỹ liên quan.
- Kịch bản bullish, bearish, no-trade: điều kiện kích hoạt, hủy, mức giá, tuổi thọ và lý do. Phân biệt kịch bản tham khảo với tín hiệu có thể mở paper order.
- Bài học phù hợp đã đọc, số mẫu, điều kiện còn thiếu, ngân sách ngày và lý do nên chờ.
- Link trực tiếp từ báo cáo tới mức giá/nến trên chart. Lưu phiên bản báo cáo, không ghi đè mất lịch sử.

## 6. SMC/ICT engine: quy tắc kiểm chứng được

Trước khi code, ghi rõ bộ quy tắc v1 trong `docs/strategy_rules.md`; cấu hình được nhưng versioned. Không biến một thuật ngữ mơ hồ thành tín hiệu không giải thích.

Quy ước ban đầu:

- Pivot high/low xác nhận bằng 2 nến trái + 2 nến phải đã đóng. `pivot_time` và `confirmed_at` khác nhau. Backtest không được dùng pivot trước `confirmed_at`.
- Xu hướng dựa trên chuỗi đỉnh/đáy xác nhận HH/HL hoặc LH/LL; trường hợp khác range/neutral. Không đủ swing thì unknown.
- BOS: nến đóng vượt swing đã xác nhận theo hướng cấu trúc. CHoCH: phá cấu trúc ngược bối cảnh trước thời điểm phá. Lưu thời điểm xác nhận, không relabel lịch sử bằng trend tương lai.
- Sweep: wick vượt swing đã xác nhận rồi close trở lại; liên kết đúng swing/time. Mẫu giá không chứng minh có chủ thể cố ý săn stop.
- FVG ba nến; có created_at, confirmed_at, hướng, biên, trạng thái partial/full mitigation, expiry. Dùng rule xác định mitigation thống nhất.
- OB: định nghĩa nến ngược chiều trước displacement/BOS, với điều kiện displacement cụ thể theo ATR và số nến. Nếu mới làm heuristic, đánh dấu OB tham khảo và không dùng riêng làm trigger.
- Dealing range và premium/discount phải định nghĩa rõ, không đổi range sau lệnh để làm đẹp kết quả.
- ATR và các bộ lọc dùng dữ liệu trước/sẵn có ở thời điểm phân tích, có warm-up.

Setup v1 tập trung **liquidity sweep → displacement/CHoCH hoặc BOS → retracement vào FVG → xác nhận vào**:

1. D và 4H đồng thuận; 1H không đối lập theo rule. Nếu xung đột: no-trade v1.
2. Có liquidity level/vùng giá xác định trước.
3. Sweep rồi structural confirmation phải đúng thứ tự và cùng kịch bản; không ghép hai sự kiện không liên quan.
4. FVG hợp lệ, còn tuổi thọ; entry ở vị trí premium/discount phù hợp.
5. Xác nhận execution trên nến 5M/1M đã đóng, đúng loại lệnh.
6. SL ngoài điểm invalidation với buffer được định nghĩa; TP theo vùng thanh khoản có khoảng cách đủ, không tạo mục tiêu tùy ý để đạt R:R.
7. R:R kỳ vọng sau fee/slippage tối thiểu 2.0 mặc định; nếu không đủ thì bỏ qua.
8. News/spread/freshness/risk/session filters đều đạt.

Có checklist pass/fail/unknown cho từng điều kiện, kèm bằng chứng candle IDs và phiên bản strategy. Có quality score nếu cần để xếp hạng, nhưng hard filters không được bypass bằng tổng điểm.

## 7. Tự đưa tín hiệu, tránh spam và giới hạn số lệnh

Tín hiệu có state machine: candidate → armed → triggered → paper_open → closed; có expired, invalidated, rejected. Setup là đối tượng có ID ổn định; mỗi setup chỉ được trigger một lần.

Mỗi tín hiệu hiển thị: long/short, Entry hoặc entry zone, market/limit, SL, TP, quantity, notional, margin ước tính, ngân sách lỗ, R:R gross/net, fee/slippage assumptions, expiry, phiên, lý do, invalidation, tin liên quan và bài học liên quan.

- Không gọi vùng chờ là “lệnh chắc chắn”. Signal executable chỉ phát sau hard filters.
- Tối đa 3 lệnh được fill/ngày UTC+7, tính cả đóng/mở lại và partial initial fills theo order rules; không reset bộ đếm khi restart.
- Mặc định một vị thế mở; không pyramiding, averaging down hoặc martingale.
- Cooldown mặc định 30 phút sau khi đóng; sau 2 lệnh lỗ liên tiếp trong ngày thì dừng mở mới cho ngày đó.
- Không ưu tiên một lệnh chỉ để đủ quota. Báo cáo ngày giải thích “0/1 lệnh vì …” khi cần.
- Idempotency, transaction và unique constraints chống double submit, duplicate WS và scheduler chạy trùng.
- Có nút Pause và emergency stop để ngừng mở mới/hủy pending paper orders. Không tự đóng vị thế đang mở chỉ vì Pause; quản trị SL/TP vẫn hoạt động khi feed khỏe. Emergency close là thao tác riêng.

## 8. Quản trị rủi ro và paper broker

Mặc định có thể chỉnh:

- Vốn giả lập: 1.000 USDT.
- Rủi ro mỗi lệnh: 0,5% equity; hard maximum cấu hình 1% cho v1.
- Giới hạn lỗ ngày: 1,5% equity đầu ngày; mở mới bị chặn nếu worst-case loss của lệnh mới cộng lỗ đã ghi nhận có thể vượt ngân sách ngày.
- Tối đa 3 lần mở/ngày, một vị thế; đòn bẩy kiểm tra ký quỹ mặc định 3x.
- Không tự nới risk parameters theo lời/lỗ.

Tính quantity từ khoảng entry–SL + fee + slippage + funding dự kiến nếu khả dụng, làm tròn xuống theo bước sàn. Kiểm tra minimum quantity/notional, available margin và risk sau rounding. Entry market giả lập buy tại ask, sell tại bid, có slippage; close long tại bid và short tại ask. Nếu bid/ask bất thường hoặc cũ thì không fill.

Paper broker cần event log fill/partial/reject/cancel; limit orders mô phỏng có quy tắc bảo thủ, không cho rằng chỉ chạm giá là chắc khớp. Lưu giả định về liquidity.

- Stop gap: fill theo giá có thể quan sát đầu tiên sau trigger, không bảo đảm đúng giá SL.
- Nếu cùng OHLC bar chạm TP và SL mà thiếu tick xác định thứ tự: đánh dấu ambiguous, dùng giả định bảo thủ SL-first và báo rõ trong thống kê.
- Đứt feed hoặc máy sleep: ghi gap, không phát minh fills. Khi reconnect, reconcile theo dữ liệu đã có, trạng thái uncertain nếu không xác định được; chặn lệnh mới tới khi giải quyết.
- Tách last/mark/index; nếu chưa mô phỏng thanh lý/funding đầy đủ phải ghi rõ. Không gọi paper simulator là Bitget demo account.
- Equity, realized/unrealized P&L, fees, funding và drawdown phải nhất quán; dùng UTC+7 cho ngày giao dịch, UTC lưu trữ.

## 9. Lịch tin kinh tế: API + import file

Tạo `EconomicCalendarProvider` để có thể đổi nhà cung cấp. Bản đầu phải chạy được bằng **import JSON/CSV thủ công**, không đòi paid API.

Nguồn tham khảo:
- Forex Factory official weekly export: https://nfs.faireconomy.media/ff_calendar_thisweek.json . Kiểm tra schema thực tế.
- Trading Economics: https://docs.tradingeconomics.com/economic_calendar/
- FMP, EODHD.

Import phải có:
- File picker/drag-drop JSON hoặc CSV; preview mapping, timezone, date range; xác nhận trước commit.
- Auto-detect Forex Factory format và canonical format.
- ...

## 10. Nghiên cứu tác động tin lên XAUUSDT

Tách hai chức năng:
**Trước công bố:** xác định news risk, chuẩn bị kịch bản và blackout; không dự đoán actual như sự thật.
**Sau công bố:** phân tích số liệu và phản ứng đã quan sát; không biến tương quan thành quan hệ nhân quả.

Nhóm ưu tiên nghiên cứu ban đầu: CPI/Core CPI, PCE/Core PCE, NFP, unemployment, earnings, FOMC/rate decision/press conference/minutes, retail sales, ISM, GDP, jobless claims.

## 11. Nhật ký, bài học và khả năng học

Trước lệnh lưu snapshot bất biến.
Sau lệnh lưu Entry/exit/fills, P&L.
Trước mỗi candidate/trade, bắt buộc truy xuất lesson memory.

## 12. Thư viện kiến thức cho người hiểu căn bản
...

## 13. Persistence, vận hành và API local
Schema có migrations cho: candles, instrument_configs, quotes...
API đủ cho dashboard, chart data, daily/session reports...

## 14. Backtest/replay và kiểm thử bắt buộc
Implement replay tối thiểu dùng cùng strategy/risk engines.
Kiểm thử có ý nghĩa.

## 15. Cách thực hiện và bàn giao
1. Kiểm tra workspace/runtime và tài liệu API/library hiện hành; ghi kế hoạch ngắn và bắt đầu.
2. Làm một vertical slice chạy được: chart live XAUUSDT + storage + health. Sau đó thêm analysis/report, news import, signals/risk, auto paper, journal/lessons, research/replay và knowledge.
...
