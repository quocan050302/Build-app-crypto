import { describe, it, expect, vi } from 'vitest';
import {
  apiClient,
  api
} from './api/client';

describe('V8 Frontend Client & Tab Contract Verification', () => {
  it('correctly maps TelegramConfig with has_token and masked token security', async () => {
    const mockConfig = {
      enabled: true,
      bot_token_masked: '1234...5678',
      chat_id: '6919390280',
      subscribed_events: ['READY', 'FILLED', 'TP_HIT', 'SL_HIT', 'MANUAL_CLOSED'],
      quiet_hours_enabled: false,
      quiet_hours_start: '23:00',
      quiet_hours_end: '06:00',
      bypass_critical_quiet_hours: true,
      near_entry_mode: 'ATR',
      near_entry_atr_mult: 0.5,
      near_entry_price_dist: 2.0,
      near_entry_cooldown_min: 30,
      timezone: 'Asia/Ho_Chi_Minh',
      has_token: true,
      token_configured: true,
    };

    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockConfig });

    const cfg = await api.getTelegramConfig();
    expect(cfg.has_token).toBe(true);
    expect(cfg.token_configured).toBe(true);
    expect(cfg.bot_token_masked).toBe('1234...5678');
    expect(cfg.chat_id).toBe('6919390280');
  });

  it('sends clear_token flag without sending masked token placeholder', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      data: {
        status: 'success',
        message: 'Saved',
        config: {
          enabled: false,
          bot_token_masked: '',
          chat_id: '6919390280',
          has_token: false,
          token_configured: false,
          subscribed_events: [],
          quiet_hours_enabled: false,
          quiet_hours_start: '23:00',
          quiet_hours_end: '06:00',
          bypass_critical_quiet_hours: true,
          near_entry_mode: 'ATR',
          near_entry_atr_mult: 0.5,
          near_entry_price_dist: 2.0,
          near_entry_cooldown_min: 30,
          timezone: 'Asia/Ho_Chi_Minh',
        }
      }
    });

    const res = await api.updateTelegramConfig({
      clear_token: true,
      enabled: false,
      chat_id: '6919390280'
    });

    expect(postSpy).toHaveBeenCalledWith('/api/v1/telegram/config', {
      clear_token: true,
      enabled: false,
      chat_id: '6919390280'
    });
    expect(res.has_token).toBe(false);
  });

  it('correctly retrieves paginated journal with server-side summary calculations', async () => {
    const mockJournalResponse = {
      items: [
        {
          id: 'ord-1',
          instrument: 'XAUUSDT',
          direction: 'LONG',
          state: 'closed',
          order_type: 'LIMIT',
          timeframe: '15M',
          planned_entry: 4120.0,
          actual_entry: 4120.0,
          actual_exit: 4150.0,
          stop_loss: 4100.0,
          take_profit: 4150.0,
          quantity: 0.1,
          initial_risk_usdt: 10.0,
          realized_pnl_net: 50.0,
          realized_r: 2.5,
          exit_cause: 'TP_HIT',
          created_at: 1728447000000,
          closed_at: 1728448000000,
        },
        {
          id: 'ord-2',
          instrument: 'XAUUSDT',
          direction: 'SHORT',
          state: 'closed',
          order_type: 'LIMIT',
          timeframe: '15M',
          planned_entry: 4150.0,
          actual_entry: 4150.0,
          actual_exit: 4140.0,
          stop_loss: 4170.0,
          take_profit: 4110.0,
          quantity: 0.1,
          initial_risk_usdt: 10.0,
          realized_pnl_net: 10.0,
          realized_r: 0.5,
          exit_cause: 'MANUAL_CLOSE',
          created_at: 1728449000000,
          closed_at: 1728450000000,
        },
      ],
      total: 2,
      page: 1,
      page_size: 20,
      total_pages: 1,
      summary: {
        total_filtered: 2,
        completed_count: 2,
        open_count: 0,
        pending_count: 0,
        net_pnl: 60.0,
        win_count: 2,
        loss_count: 0,
        breakeven_count: 0,
        wins: 2,
        losses: 0,
        breakevens: 0,
        winrate_pct: 100.0,
        avg_realized_r: 1.5,
        average_realized_r: 1.5,
      }
    };

    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockJournalResponse });

    const journal = await api.getJournalPaginated({ page: 1, page_size: 20 });
    expect(journal.total).toBe(2);
    expect(journal.items.length).toBe(2);
    expect(journal.summary.net_pnl).toBe(60.0);
    expect(journal.summary.winrate_pct).toBe(100.0);
  });

  it('manages TradeReview and preserves optimistic concurrency revision', async () => {
    const mockReview = {
      id: 'rev-uuid-1',
      trade_id: 'ord-1',
      execution_mode: 'AUTO',
      user_notes: 'Tuân thủ đúng kỷ luật',
      self_reported_entry_reason: 'Sweep phiên Á',
      psychology_before: 'Bình tĩnh',
      psychology_during: 'Kiên nhẫn',
      psychology_after: 'Tốt',
      emotions: ['Bình tĩnh', 'Kỷ luật cao'],
      confidence_score: 5,
      discipline_score: 5,
      revision: 1,
      created_at: 1728447000000,
      updated_at: 1728447000000,
      reviewed_at: 1728447000000,
    };

    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockReview });
    const fetched = await api.getTradeReview('ord-1');
    expect(fetched?.revision).toBe(1);
    expect(fetched?.user_notes).toBe('Tuân thủ đúng kỷ luật');

    // Update review with expected_revision
    vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      data: { ...mockReview, revision: 2, user_notes: 'Bổ sung kế hoạch' }
    });

    const updated = await api.saveTradeReview('ord-1', {
      user_notes: 'Bổ sung kế hoạch',
      expected_revision: 1,
    });
    expect(updated.revision).toBe(2);
    expect(updated.user_notes).toBe('Bổ sung kế hoạch');
  });

  it('handles lesson lifecycle actions: approve, reject, archive', async () => {
    vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      data: { id: 10, status: 'APPROVED', is_approved: true }
    });
    const approved = await api.approveLesson(10);
    expect(approved.status).toBe('APPROVED');
    expect(approved.is_approved).toBe(true);

    vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      data: { id: 11, status: 'REJECTED', is_approved: false }
    });
    const rejected = await api.rejectLesson(11);
    expect(rejected.status).toBe('REJECTED');

    vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      data: { id: 12, status: 'ARCHIVED', is_approved: false }
    });
    const archived = await api.archiveLesson(12);
    expect(archived.status).toBe('ARCHIVED');
  });
});
