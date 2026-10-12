from typing import Optional, Dict, Any, List
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState

def explain_missing_condition(reason_or_state: str) -> Dict[str, str]:
    mapping = {
        'WAITING_LIQUIDITY': {
            'code': 'WAITING_LIQUIDITY',
            'explanation': 'Chưa xác định được vùng thanh khoản anchor quan sát hợp lệ.',
            'next_step': 'Chờ tạo swing đỉnh/đáy mới hoặc mở phiên tiếp theo để xác định biên.',
        },
        'WAITING_SWEEP': {
            'code': 'WAITING_SWEEP',
            'explanation': 'Đang theo dõi thanh khoản; chưa có nến quét qua mức giá anchor và rút chân.',
            'next_step': 'Quan sát nến kiểm tra vùng thanh khoản, yêu cầu wick xuyên qua và đóng quay lại.',
        },
        'WAITING_BREAK': {
            'code': 'WAITING_BREAK',
            'explanation': 'Đã có quét thanh khoản; đang chờ cấu trúc bị phá vỡ (CHoCH/MSS) kèm displacement nến thân lớn.',
            'next_step': 'Chờ nến đóng cửa vượt qua đỉnh/đáy swing đối diện với thân nến đạt chuẩn ATR.',
        },
        'WAITING_FVG': {
            'code': 'WAITING_FVG',
            'explanation': 'Cấu trúc đã break nhưng chưa hình thành khoảng trống thanh khoản (FVG) hợp lệ thuộc nhịp đẩy.',
            'next_step': 'Chờ cụm 3 nến tạo khoảng trống FVG cùng hướng nhịp displacement.',
        },
        'WAITING_RETEST': {
            'code': 'WAITING_RETEST',
            'explanation': 'FVG đã xác nhận; giá chưa quay trở lại retest vùng FVG.',
            'next_step': 'Chờ giá hồi về chạm vùng FVG với nến hoặc quote hợp lệ.',
        },
        'UNKNOWN_HTF': {
            'code': 'UNKNOWN_HTF',
            'explanation': 'Thiếu dữ liệu nến đóng khung lớn H4/H1 hoặc xu hướng chưa đồng thuận.',
            'next_step': 'Kiểm tra kết nối dữ liệu khung lớn hoặc chờ nến H1/H4 đóng.',
        },
        'DATA_GAP': {
            'code': 'DATA_GAP',
            'explanation': 'Phát hiện thiếu nến hoặc khoảng trống dữ liệu trong chuỗi thời gian.',
            'next_step': 'Hệ thống tự động tải bù nến; tạm dừng xác nhận cấu trúc.',
        },
        'NO_TARGET': {
            'code': 'NO_TARGET',
            'explanation': 'Chưa tìm thấy vùng thanh khoản mục tiêu chưa bị lấy thỏa mãn tỷ lệ R:R tối thiểu.',
            'next_step': 'Chờ xuất hiện swing ngược hướng mới làm mục tiêu thoát lệnh hợp lệ.',
        },
        'NEWS_BLOCKED': {
            'code': 'NEWS_BLOCKED',
            'explanation': 'Cấu trúc SMC hợp lệ nhưng đang trong cửa sổ tin tức đỏ bị chặn giao dịch.',
            'next_step': 'Chờ qua cửa sổ tin tức đỏ (theo chính sách kiểm soát rủi ro).',
        },
        'LESSON_BLOCKED': {
            'code': 'LESSON_BLOCKED',
            'explanation': 'Bị chặn bởi bài học kỷ luật đang kích hoạt (red lesson active).',
            'next_step': 'Giải quyết hoặc chờ thời gian hết hạn bài học rủi ro.',
        },
        'BLOCKED': {
            'code': 'BLOCKED',
            'explanation': 'Kịch bản cấu trúc đủ nhưng bị chặn bởi các quy tắc an toàn hoặc rủi ro.',
            'next_step': 'Xem danh sách blockers chi tiết trong drawer để gỡ bỏ.',
        },
        'INVALIDATED': {
            'code': 'INVALIDATED',
            'explanation': 'Giá đã vi phạm mức vô hiệu hóa (invalidation level) hoặc cấu trúc bị phá vỡ.',
            'next_step': 'Hủy kịch bản hiện tại; theo dõi kịch bản mới khi có cấu trúc mới.',
        },
        'EXPIRED': {
            'code': 'EXPIRED',
            'explanation': 'Kịch bản đã hết thời hạn hiệu lực (ngoài phiên giao dịch NY).',
            'next_step': 'Đóng theo dõi kịch bản phiên cũ; chuyển sang phiên tiếp theo.',
        },
    }
    return mapping.get(reason_or_state, {
        'code': reason_or_state,
        'explanation': f'Điều kiện chưa đạt: {reason_or_state}.',
        'next_step': 'Theo dõi cập nhật cấu trúc nến tiếp theo.',
    })

def build_scenario_summary_vi(
    *,
    symbol: str,
    direction: Optional[str],
    state: str,
    h4_regime: str,
    h1_regime: str,
    anchor_desc: Optional[str] = None,
    anchor_price: Optional[float] = None,
    sweep_desc: Optional[str] = None,
    break_desc: Optional[str] = None,
    fvg_desc: Optional[str] = None,
    retest_desc: Optional[str] = None,
    target_desc: Optional[str] = None,
    target_price: Optional[float] = None,
    invalidation_price: Optional[float] = None,
    blockers: Optional[List[str]] = None,
    timeframe: str = '5M',
) -> str:
    lines = []
    
    # 1. Bối cảnh
    h4_text = 'tăng' if 'UP' in h4_regime else ('giảm' if 'DOWN' in h4_regime else ('đi ngang' if 'RANGE' in h4_regime else 'chưa rõ'))
    h1_text = 'đồng thuận' if 'ALIGNED' in h1_regime or ('UP' in h1_regime and 'UP' in h4_regime) or ('DOWN' in h1_regime and 'DOWN' in h4_regime) else ('ngược chiều' if ('UP' in h1_regime and 'DOWN' in h4_regime) or ('DOWN' in h1_regime and 'UP' in h4_regime) else 'chưa rõ')
    lines.append(f'Bối cảnh: H4 {h4_text}; H1 {h1_text}.')

    # 2. Đang theo dõi
    if anchor_price and anchor_desc:
        lines.append(f'Đang theo dõi: {anchor_desc} tại {anchor_price:.2f}.')
    elif anchor_desc:
        lines.append(f'Đang theo dõi: {anchor_desc}.')
    else:
        lines.append('Đang theo dõi: đang quét cấu trúc tìm vùng thanh khoản.')

    # 3. Đã xác nhận
    confirmed_items = []
    if sweep_desc:
        confirmed_items.append(sweep_desc)
    if break_desc:
        confirmed_items.append(break_desc)
    if fvg_desc:
        confirmed_items.append(fvg_desc)
    if retest_desc:
        confirmed_items.append(retest_desc)

    if state == 'STRUCTURE_CONFIRMED':
        lines.append('Đã xác nhận: chuỗi SMC hoàn chỉnh (quét, break MSS, FVG, retest).')
    elif confirmed_items:
        lines.append(f'Đã xác nhận: {", ".join(confirmed_items)}.')
    else:
        lines.append('Đã xác nhận: chưa có xác nhận cấu trúc.')

    # 4. Cần chờ
    if blockers:
        lines.append(f'Bị chặn: {", ".join(blockers)}.')
    elif state == 'STRUCTURE_CONFIRMED':
        lines.append('Cần chờ: kiểm tra điều kiện vào lệnh (entry guard, tin tức, tài khoản).')
    elif state == 'WAITING_SWEEP':
        lines.append('Cần chờ: nến quét thanh khoản và rút chân đóng trở lại.')
    elif state == 'WAITING_BREAK':
        lines.append('Cần chờ: break cấu trúc kèm displacement nến thân lớn.')
    elif state == 'WAITING_FVG':
        lines.append('Cần chờ: hình thành FVG liên quan nhịp displacement.')
    elif state == 'WAITING_RETEST':
        lines.append('Cần chờ: giá hồi về retest vùng FVG đã xác nhận.')
    elif state == 'INVALIDATED':
        lines.append('Cần chờ: cấu trúc mới (kịch bản đã bị vô hiệu hóa).')
    elif state == 'EXPIRED':
        lines.append('Cần chờ: phiên tiếp theo (kịch bản đã hết phiên NY).')
    else:
        lines.append('Cần chờ: tín hiệu cấu trúc tiếp theo.')

    # 5. Mục tiêu
    if target_price:
        desc = target_desc if target_desc else 'vùng thanh khoản'
        lines.append(f'Mục tiêu: {desc} {target_price:.2f} còn chưa bị lấy.')
    else:
        lines.append('Mục tiêu: chưa xác định mục tiêu hợp lệ.')

    # 6. Hủy khi
    if invalidation_price:
        lines.append(f'Hủy khi: vi phạm mức vô hiệu hóa {invalidation_price:.2f} hoặc hết phiên NY.')
    else:
        lines.append('Hủy khi: vi phạm đáy/đỉnh cấu trúc quét hoặc hết phiên NY.')

    return "\n".join(lines)


def build_entry_explanation(snapshot: ScenarioSnapshot, plan: Optional[Dict[str, Any]], guards: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """
    Returns a structured context including context, tracked levels, sweep, break, FVG, target, and guard audit.
    """
    ctx = snapshot.context or {}
    h4 = ctx.get('h4_bias', 'UNKNOWN')
    h1 = ctx.get('h1_alignment', 'UNKNOWN')
    
    evidence_by_kind = {}
    for n in snapshot.evidence:
        kind = n.get('kind')
        if kind:
            evidence_by_kind[kind] = n

    sweep = evidence_by_kind.get('SWEEP_HIGH') or evidence_by_kind.get('SWEEP_LOW')
    break_node = next((n for n in snapshot.evidence if n.get('kind') in ('MSS', 'CHOCH', 'BOS')), None)
    fvg = evidence_by_kind.get('FVG')
    retest = evidence_by_kind.get('RETEST')
    
    return {
        "context": {
            "h4_bias": h4,
            "h1_alignment": h1,
            "regime": ctx.get('regime', 'UNKNOWN')
        },
        "tracked_levels": snapshot.liquidity_map,
        "evidence_summary": {
            "sweep": sweep.get('extra', {}).get('detail', 'Có') if sweep else 'Không',
            "break": break_node.get('extra', {}).get('detail', 'Có') if break_node else 'Không',
            "fvg": fvg.get('extra', {}).get('detail', 'Có') if fvg else 'Không',
            "retest": retest.get('extra', {}).get('detail', 'Có') if retest else 'Không'
        },
        "target": snapshot.target,
        "plan": plan,
        "guard_audit": guards or []
    }
