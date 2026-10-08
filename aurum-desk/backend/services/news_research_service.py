import asyncio
import hashlib
import json
import logging
import re
import time
from urllib.parse import urlparse
from typing import Dict, Any, Optional, List, Tuple
import httpx
from bs4 import BeautifulSoup
import schemas

logger = logging.getLogger(__name__)

# Domain allowlist for news/research extraction
ALLOWED_DOMAINS = [
    "forexfactory.com",
    "www.forexfactory.com",
    "investing.com",
    "www.investing.com",
    "bloomberg.com",
    "www.bloomberg.com",
    "reuters.com",
    "www.reuters.com",
    "tradingeconomics.com",
    "www.tradingeconomics.com",
    "marketwatch.com",
    "www.marketwatch.com"
]

# SSRF Protection: Block private and internal IP addresses
BLOCKED_HOST_PATTERNS = [
    r"^localhost$",
    r"^127\.",
    r"^10\.",
    r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",
    r"^192\.168\.",
    r"^169\.254\.",
    r"^0\.0\.0\.0$"
]

class NewsResearchService:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(3)  # Bounded concurrency
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.cache_ttl_sec = 3600  # 1 hour in-memory cache
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8"
        }

    def validate_url(self, url: str) -> Tuple[bool, Optional[str]]:
        """Validate URL scheme and domain allowlist, prevent SSRF."""
        if not url:
            return False, "URL rỗng."
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False, f"Giao thức không được hỗ trợ: '{parsed.scheme}'. Chỉ chấp nhận http/https."
            hostname = (parsed.hostname or "").lower()
            if not hostname:
                return False, "URL không có hostname hợp lệ."

            for pat in BLOCKED_HOST_PATTERNS:
                if re.match(pat, hostname):
                    return False, f"Địa chỉ nội bộ/private IP bị chặn vì lý do bảo mật: {hostname}"

            if not any(hostname == d or hostname.endswith(f".{d}") for d in ALLOWED_DOMAINS):
                return False, f"Tên miền '{hostname}' không nằm trong danh sách allowlist được hỗ trợ."

            return True, None
        except Exception as e:
            return False, f"URL không hợp lệ: {str(e)}"

    async def fetch_html(self, url: str, timeout: float = 10.0) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Fetch HTML with bounded concurrency and timeout.
        Returns: (html_content, status_code_or_error, error_detail)
        """
        is_valid, err = self.validate_url(url)
        if not is_valid:
            return None, "INVALID_URL", err

        # Check cache
        cache_key = hashlib.md5(url.encode()).hexdigest()
        if cache_key in self.cache:
            entry = self.cache[cache_key]
            if time.time() - entry["cached_at"] < self.cache_ttl_sec:
                return entry["html"], "CACHED", None

        async with self.semaphore:
            try:
                async with httpx.AsyncClient(headers=self.headers, timeout=timeout, follow_redirects=True) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        html = resp.text
                        self.cache[cache_key] = {"html": html, "cached_at": time.time()}
                        return html, "200", None
                    elif resp.status_code in (401, 403):
                        return None, "BLOCKED", f"Nguồn tin từ chối truy cập (HTTP {resp.status_code}). Trang yêu cầu xác thực hoặc chặn bot tự động."
                    elif resp.status_code == 429:
                        return None, "RATE_LIMITED", "Nguồn tin tạm thời giới hạn tần suất truy cập (HTTP 429)."
                    else:
                        return None, f"HTTP_{resp.status_code}", f"Nguồn tin phản hồi mã lỗi {resp.status_code}"
            except httpx.TimeoutException:
                return None, "TIMEOUT", f"Quá thời gian chờ ({timeout}s) khi kết nối tới nguồn tin."
            except httpx.ConnectError:
                return None, "CONNECT_ERROR", "Lỗi kết nối mạng tới máy chủ nguồn tin."
            except Exception as e:
                return None, "REQUEST_ERROR", f"Lỗi yêu cầu: {type(e).__name__}: {str(e)}"

    def parse_forex_factory_html(self, html: str, source_url: str) -> Dict[str, Any]:
        """
        Extract structured indicator specs, description, usual effect,
        and historical release table from Forex Factory HTML.
        """
        soup = BeautifulSoup(html, "html.parser")
        specs: Dict[str, str] = {}
        historical_releases: List[Dict[str, Any]] = []

        # 1. Indicator Details / Specs Table
        # Look for calendar specs table or description
        for spec_row in soup.select("table.calendar__spec tr, div.calendar__spec-item"):
            th = spec_row.find(["th", "td", "span", "div"], class_=re.compile(r"title|name|label", re.I))
            td = spec_row.find(["td", "div"], class_=re.compile(r"value|detail|desc", re.I))
            if th and td:
                key = th.get_text(strip=True).rstrip(":")
                val = td.get_text(strip=True)
                if key and val:
                    specs[key] = val

        # If not structured into table, search general specs
        desc_el = soup.select_one("div.calendar__spec-description, div.calendar__detail")
        description = desc_el.get_text(strip=True) if desc_el else specs.get("Measures", specs.get("Description", ""))
        usual_effect = specs.get("Usual Effect", "")
        source_name = specs.get("Source", "Forex Factory")

        # 2. Historical Releases Table
        # Table of past actual vs forecast vs previous
        for row in soup.select("table.calendar__history tr, table.calendar__table tr"):
            cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cols) >= 5 and any(cols[0].startswith(m) for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]):
                date_val = cols[0]
                time_val = cols[1] if len(cols) > 1 else ""
                actual_val = cols[2] if len(cols) > 2 else ""
                forecast_val = cols[3] if len(cols) > 3 else ""
                previous_val = cols[4] if len(cols) > 4 else ""

                historical_releases.append({
                    "date": date_val,
                    "time": time_val,
                    "actual": actual_val,
                    "forecast": forecast_val,
                    "previous": previous_val
                })

        return {
            "source_url": source_url,
            "description": description,
            "usual_effect": usual_effect,
            "source_name": source_name,
            "specs": specs,
            "historical_releases": historical_releases[:12]
        }

    def generate_research_assessment(
        self,
        news_id: int,
        title: str,
        country: str,
        impact: str,
        scheduled_at: int,
        source_url: Optional[str] = None,
        extracted_data: Optional[Dict[str, Any]] = None,
        actual: Optional[str] = None,
        forecast: Optional[str] = None,
        previous: Optional[str] = None
    ) -> schemas.NewsResearchResponse:
        """
        Generate structured research assessment for XAUUSDT (Gold) in Vietnamese.
        Combines indicator meaning, gold transmission channels, pre-release scenarios,
        and post-release surprise interpretation.
        """
        t_lower = title.lower()
        curr_upper = (country or "USD").upper()

        # 1. Meaning in Vietnamese
        meaning = "Chỉ số kinh tế vĩ mô phản ánh tình hình hoạt động kinh tế."
        if "cpi" in t_lower:
            meaning = "Chỉ số Giá Tiêu dùng (Consumer Price Index) đo lường sự thay đổi chi phí của giỏ hàng hóa và dịch vụ tiêu biểu. Đây là thước đo lạm phát trọng yếu được Cục Dự trữ Liên bang Mỹ (Fed) đặc biệt chú trọng khi hoạch định chính sách lãi suất."
        elif "non-farm" in t_lower or "nfp" in t_lower or "employment change" in t_lower:
            meaning = "Báo cáo Bảng lương Phi nông nghiệp (Non-Farm Payrolls - NFP) đo lường số lượng việc làm mới được tạo ra trong tháng (ngoại trừ ngành nông nghiệp). Đây là chỉ số sức khỏe then chốt của thị trường lao động Mỹ và là động lực tạo sóng biến động mạnh nhất tháng."
        elif "unemployment rate" in t_lower:
            meaning = "Tỷ lệ Thất nghiệp (Unemployment Rate) đo lường tỷ lệ phần trăm lực lượng lao động đang tích cực tìm kiếm việc làm nhưng chưa có việc. Tỷ lệ thất nghiệp thấp biểu hiện thị trường lao động thắt chặt."
        elif "unemployment claims" in t_lower:
            meaning = "Số đơn xin trợ cấp thất nghiệp lần đầu (Initial Jobless Claims) theo dõi số lượng người nộp đơn lần đầu trong tuần qua, cung cấp tín hiệu sớm nhất về sức khỏe thị trường lao động Mỹ."
        elif "gdp" in t_lower:
            meaning = "Tổng Sản phẩm Quốc nội (Gross Domestic Product - GDP) đo lường tổng giá trị hàng hóa và dịch vụ được sản xuất trong một quốc gia, phản ánh tốc độ tăng trưởng kinh tế tổng thể."
        elif "retail sales" in t_lower:
            meaning = "Doanh số Bán lẻ (Retail Sales) phản ánh tổng giá trị chi tiêu của người tiêu dùng tại các cửa hàng bán lẻ, chiếm khoảng 70% hoạt động kinh tế Mỹ."
        elif "fed" in t_lower or "fomc" in t_lower or "funds rate" in t_lower:
            meaning = "Quyết định Lãi suất và Biên bản Họp của Ủy ban Thị trường Mở Liên bang (FOMC/Fed). Đây là sự kiện định hình chi phí vốn toàn cầu và có tác động trực tiếp mạnh mẽ nhất tới mọi tài sản tài chính."
        elif "ppi" in t_lower:
            meaning = "Chỉ số Giá Sản xuất (Producer Price Index - PPI) đo lường sự thay đổi giá cả từ góc độ người sản xuất, thường dẫn dắt chỉ số CPI sau 1-2 tháng."
        elif "ism" in t_lower or "pmi" in t_lower:
            meaning = "Chỉ số Nhà quản lý Mua hàng (PMI / ISM) đo lường mức độ lạc quan của các giám đốc thu mua trong ngành sản xuất hoặc dịch vụ. Ngưỡng 50 điểm phân định giữa tăng trưởng và thu hẹp."

        # If extracted specs provide description, append it
        if extracted_data and extracted_data.get("description"):
            meaning += f"\n\n*Mô tả từ nguồn:* {extracted_data['description']}"

        # 2. Gold Relevance Rating
        # Determine relevance
        from news_service import determine_gold_relevance
        relevance = determine_gold_relevance(curr_upper, impact, title)

        # 3. Transmission Channels to Gold
        channels = {}
        if curr_upper == "USD":
            channels["Kênh Chỉ số USD (DXY)"] = "Vàng được định giá bằng đồng USD ($/oz). Khi chỉ số kinh tế Mỹ vượt kỳ vọng làm đồng USD mạnh lên, giá vàng tính bằng USD thường chịu áp lực giảm giá (và ngược lại)."
            channels["Kênh Lợi suất Thực (Real Yields)"] = "Vàng là tài sản phi lãi suất (zero-yield asset). Dữ liệu kinh tế mạnh thúc đẩy kỳ vọng Fed duy trì lãi suất cao, đẩy lợi suất trái phiếu kho bạc Mỹ lên, làm tăng chi phí cơ hội nắm giữ vàng."
            channels["Kênh Phòng Hộ Lạm Phát & Trú Ẩn"] = "Khi lạm phát tăng nóng hoặc rủi ro vĩ mô xuất hiện, lực cầu tích lũy vàng vật chất và quỹ ETF vàng gia tăng như một công cụ bảo toàn giá trị."
        else:
            channels["Kênh Tỷ giá Chéo & Tâm lý Thị trường"] = f"Tin tức từ {curr_upper} tác động gián tiếp tới Vàng thông qua biến động của các cặp tỷ giá EUR/USD, GBP/USD hoặc tâm lý chấp nhận rủi ro (Risk-on/Risk-off) toàn cầu."

        # 4. Pre-Release Scenarios
        scenarios = [
            f"Kịch bản 1 (Hawkish - Tích cực cho USD): Số liệu công bố cao hơn đáng kể so với dự báo ({forecast or 'Dự báo'}) ➔ Đồng USD tăng vọt, Lợi suất trái phiếu tăng ➔ Gây áp lực giảm mạnh lên XAUUSDT.",
            f"Kịch bản 2 (Dovish - Tiêu cực cho USD): Số liệu công bố thấp hơn kỳ vọng ➔ Đồng USD suy yếu, kỳ vọng Fed nới lỏng gia tăng ➔ Tạo động lực bật tăng cho XAUUSDT.",
            f"Kịch bản 3 (In-line - Sát kỳ vọng): Số liệu sát mức dự báo ({forecast or 'Dự báo'}) ➔ Thị trường phản ứng ngắn hạn rồi tiếp tục tôn trọng cấu trúc kỹ thuật SMC hiện hành."
        ]

        # 5. Post-Release Assessment
        post_release = None
        if actual:
            post_release = f"Số liệu thực tế đã công bố: {actual} (Dự báo: {forecast or 'N/A'}, Kỳ trước: {previous or 'N/A'})."
            if extracted_data and extracted_data.get("usual_effect"):
                post_release += f" Hiệu ứng thông lệ: {extracted_data['usual_effect']}."

        # Citations
        citations = []
        if source_url:
            citations.append(f"Forex Factory Calendar: {source_url}")
        citations.append("Bitget Classic Futures XAUUSDT Market Data")

        limitations = (
            "Nghiên cứu vĩ mô phục vụ xác định bối cảnh thị trường và kích hoạt Blackout an toàn. "
            "Tín hiệu giao dịch luôn bắt buộc phải thỏa mãn đầy đủ các quy tắc xác nhận cấu trúc SMC (Sweep, MSS, FVG) "
            "và bộ lọc Net R:R tối thiểu 1:2.0."
        )

        return schemas.NewsResearchResponse(
            news_id=news_id,
            title=title,
            country=country,
            impact=impact,
            scheduled_at=scheduled_at,
            source_url=source_url,
            gold_relevance=relevance,
            research_status="SUCCEEDED" if extracted_data else "PARTIAL",
            meaning_vn=meaning,
            transmission_channels=channels,
            pre_release_scenarios=scenarios,
            post_release_assessment=post_release,
            citations=citations,
            historical_releases=extracted_data.get("historical_releases", []) if extracted_data else [],
            limitations=limitations,
            fetched_at=int(time.time() * 1000)
        )

    async def execute_research_for_news_item(
        self,
        news_item: Any,
        db: Any
    ) -> schemas.NewsResearchResponse:
        """
        Fetch source URL, extract content, and build research assessment.
        Saves updated assessment back to the database.
        """
        extracted_data = None
        url = news_item.source_url

        if url:
            html, status, err = await self.fetch_html(url)
            if html and status in ("200", "CACHED"):
                try:
                    extracted_data = self.parse_forex_factory_html(html, url)
                    news_item.research_status = "SUCCEEDED"
                except Exception as e:
                    logger.error(f"Failed to parse HTML for {url}: {e}")
                    news_item.research_status = "PARTIAL"
            elif status == "BLOCKED":
                news_item.research_status = "BLOCKED"
            else:
                news_item.research_status = "FAILED"
        else:
            news_item.research_status = "PARTIAL"

        assessment_resp = self.generate_research_assessment(
            news_id=news_item.id,
            title=news_item.title,
            country=news_item.country,
            impact=news_item.impact,
            scheduled_at=news_item.scheduled_at,
            source_url=url,
            extracted_data=extracted_data,
            actual=news_item.actual,
            forecast=news_item.forecast,
            previous=news_item.previous
        )

        # Persist assessment to database
        news_item.research_assessment = assessment_resp.model_dump_json()
        news_item.research_fetched_at = assessment_resp.fetched_at
        news_item.gold_relevance = assessment_resp.gold_relevance
        db.commit()

        return assessment_resp

news_research_service = NewsResearchService()
