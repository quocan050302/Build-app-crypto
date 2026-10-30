# V17.9.0.1.3 Raw Input & Configuration Hash Preservation Report

- **Final Code SHA:** `a499f06951d50779a143828a10fb5ca48944d87c`
- **Evaluator Version:** `17.9.0.1.1`

## Non-Overwriting Raw Hash Invariant (`DEF-179013-03`)
- All 5 compared runtimes retain their untouched raw source hashes (`raw_input_hash`, `raw_configuration_hash`).
- An explicit versioned semantic normalizer computes `canonical_semantic_input_hash` and `canonical_semantic_config_hash` from underlying normalized context.
- `validate_comparison_record_hashes()` verifies both raw hash presence and canonical semantic hash equivalence across runtimes, failing closed if any raw historical hash is overwritten (`N75`–`N77`).
