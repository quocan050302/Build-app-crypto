# V16.24 CG-01 Paper Run Report

## Execution Context
- **Software Version**: `V16.24`
- **Execution Mode**: `PAPER_ONLY`
- **Active Environment**: `TEST_FIXTURE_ISOLATION`
- **Forward Market Observation**: `NOT_OBSERVED`
- **Forward Trading Performance**: `NOT_EVALUATED` (Technical verification passing; 0 forward trading conclusions)

## Verified Lifecycle Scenarios (Automatic Event-Driven Scheduler Execution)
1. **LONG TP Lifecycle**: Real candle ingest -> Cache closes candle -> Scheduler loop automatically awakes -> Arms LONG order -> Quote Tick Fill -> Broker TP Tick Exit -> Net PnL positive -> Projection committed -> Fresh DB session report verified.
2. **SHORT SL Lifecycle**: Short setup -> Closed candle -> Scheduler Arm -> Quote Tick Fill -> Broker SL Exit -> Net PnL negative -> Invariant `TP < entry < SL` maintained.
3. **Shadow Isolation**: Pure observation cycle -> Zero orders created -> Active watchlist unmutated -> Telemetry persisted with `observation_mode='SHADOW'`.
4. **Auto-Off Guard**: Valid candidate detected but `auto_enabled=False` -> Arming blocked -> Candidate recorded as un-armed.
5. **News Guard at Execution Time**: High-impact news event within blackout window at execution time -> Arming blocked with `GUARD_BLOCKED`.
6. **Daily Cap & Open Position Invariants**: Today fills reach daily max limit -> Further arming blocked; open position blocks secondary entries.
7. **Restart Durability**: Committed traces survive in DB across memory clearing and restart.
8. **Shutdown Boundary**: Telemetry persistence worker and active batch handles cleanly drain before shutdown finalization.
