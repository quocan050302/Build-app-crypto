# V16.21 CG-01 Paper Run Report

## Execution Context
- **Software Version**: `V16.21`
- **Execution Mode**: `PAPER_ONLY`
- **Active Environment**: `TEST_FIXTURE_ISOLATION`
- **Forward Market Observation**: `NOT_OBSERVED`
- **Forward Trading Performance**: `NOT_EVALUATED` (Zero forward trades or live market executions during technical test runs)

## Verified Lifecycle Scenarios
1. **LONG TP Lifecycle**: Real candle ingest -> Cache -> Scheduler -> Arm -> Quote Tick Fill -> Broker TP Tick Exit -> Net PnL positive -> Projection committed -> Restart verified.
2. **SHORT SL Lifecycle**: Short candidate -> Arm -> Quote Tick Fill -> Broker SL Exit -> Net PnL negative -> Invariant `TP < entry < SL` maintained.
3. **Shadow Isolation**: Pure observation cycle -> Zero orders created -> Active watchlist unmutated -> Telemetry persisted.
4. **Auto-Off Guard**: Valid candidate detected but `auto_enabled=False` -> Arming blocked -> Candidate recorded as un-armed.
5. **News Guard Blackout**: High-impact news event within blackout window -> Arming blocked with `GUARD_BLOCKED`.
6. **Daily Cap Guard**: Today fills reach daily max limit -> Further arming blocked.
7. **Single Open Position Guard**: Existing open position -> Secondary candidate arming blocked.
