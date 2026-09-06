# Operational Checklist: 100 Concurrent Actors

## Pre-Deployment Verification

### Before going live with 100 actors, verify:

- [ ] **Actor Tick Latency Test Passes**
  - Run: `pytest tests/unit/test_operational_load.py::TestLoadActorTick::test_100_actors -v`
  - Expected: P99 < 200ms ✅
  - If fails: Scale back to 50 actors, investigate

- [ ] **Memory Test Passes**
  - Run: `pytest tests/unit/test_operational_load.py::TestLoadMemoryGrowth::test_memory_at_100_actors -v`
  - Expected: Growth < 10.24 MB ✅
  - If fails: Check for memory leaks

- [ ] **Planet Tick Test Passes**
  - Run: `pytest tests/unit/test_operational_load.py::TestLoadPlanetTick::test_planet_tick_100_actors -v`
  - Expected: Single tick < 10s ✅
  - If fails: Reduce actor count to 50

- [ ] **Logging Configured**
  - [ ] Enable latency monitoring in `performance_budgets.py`
  - [ ] Set up alerts for "Previous planetary tick still running"
  - [ ] Enable per-actor cost tracking

- [ ] **Tick Interval Verified**
  - [ ] Confirm auto-tick interval is 300 seconds
  - [ ] Verify tick scheduler is running
  - [ ] Check cron/scheduler configuration

- [ ] **LLM Backend Available**
  - [ ] Ollama service running (or alternative backend)
  - [ ] Verify inference latency: `time ollama run <model> "test"`
  - [ ] Expected: < 5 seconds per request (for 100 actors to complete in 450s)

---

## During Operation: Monitoring Checklist

### Daily Checks

- [ ] **No "Skipping" Messages in Logs**
  ```bash
  # Check application logs for tick pile-up
  grep -i "skipping" logs/app.log
  # If found: Tick pile-up is occurring, take action below
  ```

- [ ] **Cycle Time Under 300 Seconds**
  - Monitor: `planetary_cycle_duration_ms` metric
  - Alert threshold: > 250,000ms (250 seconds)
  - If exceeded: Tick pile-up risk increasing

- [ ] **Per-Actor Time Under 4500ms**
  - Monitor: `planetary.cycle_per_actor` latency histogram
  - P95 should stay < 4500ms
  - If increasing: Investigate LLM backend performance

- [ ] **Memory Stable**
  - Monitor: Heap size growth per cycle
  - Alert threshold: > 20 MB growth in 1 hour
  - If detected: Possible memory leak

- [ ] **No Dropped Actors**
  - Verify: All 100 actors complete each cycle
  - Check: No "actor_id not found" errors
  - Count: actors_ticked == 100 in cycle results

---

## Troubleshooting: If Problems Occur

### Problem: "Previous planetary tick still running, skipping this cycle"

**Cause:** Tick pile-up (cycle time > 300s interval)

**Immediate Actions:**

- [ ] Check current cycle time: `grep "cycle completed" logs/ | tail -1`
- [ ] If > 300s: Activate Option A (parallelize) or reduce actors
- [ ] Verify LLM backend performance: `time ollama run <model> "test"`
- [ ] If LLM slow: Restart backend or switch to quantized model

**Short-term Mitigation (choose one):**

1. **Reduce actor count to 67** (immediate, no code changes)
   ```python
   # In deployment config
   MAX_ACTORS = 67
   ```

2. **Increase tick interval to 600s** (doubles response latency)
   ```python
   # In tick scheduler configuration
   TICK_INTERVAL_SECONDS = 600
   ```

3. **Reduce planning complexity per actor**
   ```python
   # Reduce action space or planning horizon
   # This requires development time
   ```

**Long-term Fix:**

- [ ] Deploy parallelized planetary ticker (asyncio.gather)
- [ ] Implement LLM result caching
- [ ] Profile and optimize per-actor cost

---

### Problem: P99 Latency Exceeds 200ms

**Cause:** High concurrent load or slow backend

**Investigation Steps:**

1. Check actor count:
   ```bash
   curl http://localhost:8000/api/v1/agentos/actors | jq '.length'
   ```

2. Check response times:
   ```bash
   # Sample 10 actor ticks and measure
   for i in {1..10}; do time curl http://localhost:8000/api/v1/agentos/actors/1/tick; done
   ```

3. Check backend performance:
   ```bash
   time curl http://localhost:8000/api/v1/agentos/planet/tick
   ```

**Mitigation:**

- [ ] Reduce concurrent requests (rate limit)
- [ ] Scale up infrastructure (add CPU/memory)
- [ ] Reduce actor planning complexity
- [ ] Switch to faster model (quantized, smaller)

---

### Problem: Memory Usage Growing

**Cause:** Potential memory leak or excessive per-actor memory

**Investigation:**

1. Run memory test:
   ```bash
   pytest tests/unit/test_operational_load.py::TestLoadMemoryGrowth::test_memory_at_100_actors -v
   ```

2. Check growth per cycle:
   ```python
   # Monitor heap size before/after cycle
   memory_before = psutil.Process().memory_info().rss
   # Run cycle
   memory_after = psutil.Process().memory_info().rss
   growth_mb = (memory_after - memory_before) / 1024 / 1024
   print(f"Memory growth: {growth_mb:.1f} MB per cycle")
   # Should be < 10 MB
   ```

3. Check for leaks:
   ```bash
   # Run with tracemalloc to find leaking objects
   python -m tracemalloc main.py
   ```

**Mitigation:**

- [ ] Restart services (clears any temporary leaks)
- [ ] Profile memory usage per actor
- [ ] Implement garbage collection tuning
- [ ] Scale to cluster (distribute actors across nodes)

---

### Problem: Some Actors Not Ticking

**Cause:** Actor creation failed, network issues, or resource limits

**Investigation:**

1. Count actors in database:
   ```bash
   # Query actor table
   select count(*) from actors;
   ```

2. Check for errors in actor creation:
   ```bash
   grep -i "actor" logs/app.log | grep -i "error" | head -10
   ```

3. Verify tick succeeded:
   ```bash
   # After cycle, check completion status
   curl http://localhost:8000/api/v1/agentos/planet/tick
   # Response should show actors_ticked == expected count
   ```

**Mitigation:**

- [ ] Restart services
- [ ] Check database connectivity
- [ ] Verify network connectivity between services
- [ ] Increase connection pool size if needed

---

## Performance Targets Cheat Sheet

| Metric | Target | Alert | Action |
|--------|--------|-------|--------|
| P99 actor tick latency | < 200ms | > 250ms | Check load, reduce actors |
| P95 actor tick latency | < 150ms | > 180ms | Investigate backend |
| Cycle time (P95) | < 300s | > 250s | Check tick pile-up |
| Memory per 100 actors | < 10.24 MB | > 12 MB | Investigate leak |
| LLM inference latency | < 5s | > 6s | Restart backend |
| Skipped ticks | 0 | > 0 | Reduce actors |

---

## Escalation Path

### If Problem Not Resolved in 15 Minutes:

1. **Page on-call engineer** (if production)
   - Reference: This checklist
   - Include: Logs, metrics, actor count

2. **Execute emergency mitigation:**
   - Reduce actor count to 50 (2-minute restart)
   - Increases safety margin, buys time for investigation

3. **Gather diagnostic data:**
   ```bash
   # Collect logs and metrics
   journalctl -u actor-service -n 1000 > actor-service.log
   curl http://localhost:8000/api/v1/agentos/metrics > metrics.json
   ps aux | grep python > processes.txt
   ```

4. **Contact development team:**
   - Share diagnostic data
   - Reference: LATENCY_ANALYSIS_100_ACTORS.md
   - May require code deploy to fix

---

## Maintenance Interval

### Weekly

- [ ] Review cycle time metrics
- [ ] Check for memory drift
- [ ] Verify no skipped ticks
- [ ] Profile top N slow actors (if applicable)

### Monthly

- [ ] Run full load test suite
  ```bash
  pytest tests/unit/test_operational_load.py -v
  ```

- [ ] Review performance budget compliance
- [ ] Update metrics dashboard
- [ ] Plan scaling improvements (if needed)

### Quarterly

- [ ] Run 24-hour sustained load test
- [ ] Stress test with edge cases (network delays, CPU limits)
- [ ] Review and update this checklist
- [ ] Plan next optimization phase

---

## Configuration Reference

### Key Settings for 100 Actors

```python
# Actor Configuration
ACTOR_COUNT_TARGET = 100
ACTOR_CREATION_BATCH_SIZE = 10  # Spread creation over time
ACTOR_TIMEOUT_SEC = 300

# Tick Configuration
TICK_INTERVAL_SEC = 300  # 5 minutes
TICK_MAX_DURATION_SEC = 270  # Leave 30s buffer
SKIP_TICK_IF_PREVIOUS_RUNNING = true  # Enable pile-up protection

# Performance Budgets
ACTOR_TICK_P99_MS = 200
PLANETARY_CYCLE_PER_ACTOR_MS = 4500
MEMORY_GROWTH_LIMIT_KB = 10240

# Monitoring
ENABLE_LATENCY_HISTOGRAM = true
ENABLE_PER_ACTOR_METRICS = true
ALERT_ON_SKIPPED_TICKS = true
ALERT_CYCLE_TIME_THRESHOLD_SEC = 250
```

### Environment Variables

```bash
# .env or deployment config
AGENTOS_ACTOR_LIMIT=100
AGENTOS_TICK_INTERVAL=300
AGENTOS_ENABLE_PARALLEL_TICKS=false  # Set true after deploying parallelization

# Performance
AGENTOS_LLM_TIMEOUT_SEC=10
AGENTOS_ACTOR_TICK_TIMEOUT_SEC=60

# Monitoring
AGENTOS_LATENCY_MONITORING=enabled
AGENTOS_METRICS_PORT=9090
```

---

## Quick Commands

### Check Actor Count
```bash
curl http://localhost:8000/api/v1/agentos/actors | jq '.length'
```

### Measure Tick Latency
```bash
time curl -X POST http://localhost:8000/api/v1/agentos/planet/tick
```

### View Recent Cycle Times
```bash
tail -100 logs/app.log | grep "cycle completed" | tail -10
```

### Check for Pile-Up
```bash
grep "Previous.*tick.*skipping" logs/app.log | wc -l
```

### Run Full Load Test
```bash
pytest tests/unit/test_operational_load.py -v --tb=short
```

### Watch Metrics
```bash
watch -n 5 'curl http://localhost:9090/metrics | grep planetary'
```

### Check LLM Backend
```bash
curl http://localhost:11434/api/tags  # Ollama
time ollama run <model> "test prompt"
```

---

## Decision Tree: Handling Performance Issues

```
Performance Degradation Detected?
│
├─→ Is cycle_time > 300s?
│   ├─ YES → Tick pile-up (see Troubleshooting section)
│   └─ NO  → Continue
│
├─→ Is actor_tick_p99 > 200ms?
│   ├─ YES → High load or slow backend
│   │        1. Check LLM latency
│   │        2. Reduce actors to 50
│   │        3. Scale infrastructure
│   └─ NO  → Continue
│
├─→ Is memory_growth > 10MB per cycle?
│   ├─ YES → Potential memory leak
│   │        1. Run memory test
│   │        2. Profile with tracemalloc
│   │        3. Restart services
│   └─ NO  → Continue
│
└─→ Are actors_ticked < 100?
    ├─ YES → Actor creation/connectivity issue
    │        1. Check database
    │        2. Check network
    │        3. Restart services
    └─ NO  → All checks passed ✅
```

---

## Success Criteria

100 actors are operating successfully when:

- ✅ All latency tests pass (P99 < 200ms)
- ✅ Memory stable (no growth > 10MB per cycle)
- ✅ All 100 actors tick each cycle
- ✅ No "skipping" messages in logs
- ✅ Cycle time < 300 seconds (< 5 minutes)
- ✅ No dropped connections
- ✅ LLM backend responsive (< 5s per inference)

---

## Support References

- **Full Analysis:** `LATENCY_ANALYSIS_100_ACTORS.md`
- **Visual Charts:** `LATENCY_VISUAL_CHARTS.txt`
- **Executive Summary:** `LATENCY_FINDINGS_SUMMARY.md`
- **Performance Budgets:** `src/monkey_brain/kernel/fix/performance_budgets.py`
- **Test Suite:** `tests/unit/test_operational_load.py`

---

**Document Version:** 1.0  
**Last Updated:** 2026-09-06  
**Applicable Version:** 100+ actors with latency monitoring
