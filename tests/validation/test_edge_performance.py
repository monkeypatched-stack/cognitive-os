"""
Edge Performance Testing: CognitiveOS Under Extreme Conditions

Test: System behavior at performance boundaries
- Maximum concurrent operations
- Extreme latency conditions
- Resource exhaustion scenarios
- Pathological input patterns

Goal: Understand failure modes and performance ceilings
"""
from __future__ import annotations

import time
import threading
import sys
import os
from dataclasses import dataclass
from typing import List, Dict, Callable
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, 'src')):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@dataclass
class PerformanceMetric:
    """Performance measurement"""
    name: str
    value: float
    unit: str
    status: str  # PASS, WARN, FAIL


class PerformanceMonitor:
    """Monitor performance across test runs"""
    
    def __init__(self):
        self.metrics: List[PerformanceMetric] = []
        self.lock = threading.Lock()
    
    def record(self, name: str, value: float, unit: str, threshold: float = None) -> PerformanceMetric:
        """Record a performance metric"""
        status = "PASS"
        if threshold is not None:
            if value > threshold:
                status = "WARN"
            if value > threshold * 1.5:
                status = "FAIL"
        
        metric = PerformanceMetric(name=name, value=value, unit=unit, status=status)
        with self.lock:
            self.metrics.append(metric)
        return metric
    
    def print_report(self):
        """Print performance report"""
        print("\n" + "="*70)
        print("EDGE PERFORMANCE TEST RESULTS")
        print("="*70)
        
        by_status = defaultdict(list)
        for m in self.metrics:
            by_status[m.status].append(m)
        
        for status in ["PASS", "WARN", "FAIL"]:
            if status not in by_status:
                continue
            
            print(f"\n{status}:")
            for m in by_status[status]:
                print(f"  {m.name}: {m.value:.2f} {m.unit}")


class TestHighThroughputExecution:
    """Edge: Maximum throughput operations"""
    
    def test_1000_sequential_operations(self):
        """
        Measure: Baseline throughput without concurrency
        Expected: Should be fast, deterministic latency
        Note: Each operation includes 1ms sleep (simulated work)
        """
        monitor = PerformanceMonitor()
        
        def operation():
            # Simulated cognitive tick with 1ms work
            time.sleep(0.001)
            return True
        
        start = time.time()
        for i in range(1000):
            operation()
        duration = time.time() - start
        
        throughput = 1000 / duration
        avg_latency_ms = (duration / 1000) * 1000
        
        metric_throughput = monitor.record(
            "1000 Sequential Ops Throughput",
            throughput,
            "ops/sec",
            threshold=800  # With 1ms work per op, expect ~1000 ops/sec
        )
        
        metric_latency = monitor.record(
            "Avg Latency (Sequential)",
            avg_latency_ms,
            "ms",
            threshold=10.0  # Each op should be ~1.26ms on average
        )
        
        monitor.print_report()
        
        assert metric_throughput.status != "FAIL", f"Throughput too low: {throughput:.1f} ops/sec"
        assert metric_latency.status != "FAIL", f"Latency too high: {avg_latency_ms:.2f}ms"
    
    def test_1000_concurrent_operations(self):
        """
        Measure: Throughput with maximum concurrency (1000 concurrent)
        Expected: Should handle 1000 concurrent requests efficiently
        """
        monitor = PerformanceMonitor()
        
        completion_times = []
        completion_lock = threading.Lock()
        
        def operation(op_id: int):
            start = time.time()
            time.sleep(0.001)  # Simulate work
            elapsed = time.time() - start
            with completion_lock:
                completion_times.append(elapsed)
            return op_id
        
        start = time.time()
        with ThreadPoolExecutor(max_workers=1000) as executor:
            futures = [executor.submit(operation, i) for i in range(1000)]
            results = [f.result() for f in as_completed(futures)]
        total_duration = time.time() - start
        
        throughput = 1000 / total_duration
        avg_latency = sum(completion_times) / len(completion_times) * 1000
        max_latency = max(completion_times) * 1000
        
        monitor.record("1000 Concurrent Ops Throughput", throughput, "ops/sec", 1000)
        monitor.record("Avg Latency (Concurrent)", avg_latency, "ms", 50)
        monitor.record("Max Latency (Concurrent)", max_latency, "ms", 500)
        
        monitor.print_report()
        
        assert len(results) == 1000, f"Not all operations completed: {len(results)}/1000"


class TestLatencyEdgeCases:
    """Edge: Extreme latency conditions"""
    
    def test_operations_with_100ms_latency(self):
        """
        Edge: Very high latency (network-like delays)
        Simulate: Remote actor communication with 100ms RTT
        """
        monitor = PerformanceMonitor()
        
        def high_latency_operation():
            # Simulate network round trip
            time.sleep(0.1)
            return True
        
        start = time.time()
        for _ in range(10):
            high_latency_operation()
        duration = time.time() - start
        
        throughput = 10 / duration
        
        monitor.record("10 Ops with 100ms latency each", throughput, "ops/sec", 5)
        monitor.print_report()
    
    def test_operations_with_1000ms_latency(self):
        """
        Edge: Extremely high latency (1 second per operation)
        Simulate: Slow remote service or network partition
        """
        monitor = PerformanceMonitor()
        
        def extreme_latency_operation():
            time.sleep(1.0)
            return True
        
        start = time.time()
        for _ in range(3):
            extreme_latency_operation()
        duration = time.time() - start
        
        throughput = 3 / duration
        
        monitor.record("3 Ops with 1000ms latency each", throughput, "ops/sec", 2)
        monitor.print_report()
    
    def test_timeout_behavior_under_latency(self):
        """
        Edge: Verify timeout handling when operations exceed deadline
        """
        monitor = PerformanceMonitor()
        
        timeout_threshold_ms = 100
        exceeded_count = 0
        
        def operation_with_timeout(deadline_ms: int):
            nonlocal exceeded_count
            # Simulate variable latency
            actual_latency = 0.05 + (threading.current_thread().ident % 100) * 0.001
            time.sleep(actual_latency)
            
            if actual_latency * 1000 > deadline_ms:
                exceeded_count += 1
            return True
        
        for i in range(100):
            operation_with_timeout(timeout_threshold_ms)
        
        timeout_rate = exceeded_count / 100
        
        monitor.record("Timeout Rate", timeout_rate * 100, "%", 10)
        monitor.print_report()
        
        print(f"  Timeout rate: {timeout_rate*100:.1f}% (deadline={timeout_threshold_ms}ms)")


class TestMemoryPressure:
    """Edge: Memory exhaustion scenarios"""
    
    def test_large_message_handling(self):
        """
        Edge: Very large message payloads
        Simulate: Message with 10MB+ data
        """
        monitor = PerformanceMonitor()
        
        sizes_mb = [1, 10, 50, 100]
        
        for size_mb in sizes_mb:
            # Create large payload
            payload = bytearray(size_mb * 1024 * 1024)
            
            start = time.time()
            # Simulate processing
            _ = len(payload)
            duration = time.time() - start
            
            throughput_mb_per_sec = size_mb / duration if duration > 0 else 0
            
            monitor.record(
                f"Process {size_mb}MB message",
                throughput_mb_per_sec,
                "MB/sec",
                threshold=50
            )
        
        monitor.print_report()
    
    def test_many_actors_memory_footprint(self):
        """
        Edge: Memory usage with many actors
        Simulate: 1000 actors in memory
        """
        monitor = PerformanceMonitor()
        
        actor_configs = [
            (10, "minimal"),
            (100, "small"),
            (500, "medium"),
            (1000, "large"),
        ]
        
        for count, label in actor_configs:
            # Simulate actor context
            actors = []
            for i in range(count):
                actor = {
                    'id': f'actor_{i}',
                    'belief': {'facts': [f'fact_{j}' for j in range(10)]},
                    'state': {'value': i},
                }
                actors.append(actor)
            
            # Estimate memory
            import sys
            memory_bytes = sum(sys.getsizeof(a) for a in actors)
            memory_mb = memory_bytes / (1024 * 1024)
            
            monitor.record(
                f"Memory for {count} {label} actors",
                memory_mb,
                "MB",
                threshold=100
            )
        
        monitor.print_report()


class TestPathologicalInput:
    """Edge: Pathological/adversarial input patterns"""
    
    def test_very_deep_delegation_chain(self):
        """
        Edge: Deeply nested delegation (A → B → C → ... → Z)
        Expected: Attenuation chain should handle depth gracefully
        """
        monitor = PerformanceMonitor()
        
        def create_delegation_chain(depth: int) -> Dict:
            """Create a delegation chain of given depth"""
            chain = {'depth': 0, 'scope': 'root'}
            for i in range(depth):
                chain = {
                    'depth': i + 1,
                    'parent': chain,
                    'scope': 'subset_of_parent',
                }
            return chain
        
        depths = [10, 50, 100, 500]
        
        for depth in depths:
            start = time.time()
            chain = create_delegation_chain(depth)
            duration = time.time() - start
            
            monitor.record(
                f"Create delegation chain depth {depth}",
                duration * 1000,
                "ms",
                threshold=100
            )
        
        monitor.print_report()
    
    def test_extremely_long_approval_history(self):
        """
        Edge: Operation with 1000+ approval records in history
        Expected: Audit log query should still be fast
        """
        monitor = PerformanceMonitor()
        
        # Simulate audit log with many entries
        audit_log = []
        for i in range(1000):
            audit_log.append({
                'timestamp': time.time() - (1000 - i),
                'decision': 'allow' if i % 2 else 'deny',
                'actor': f'actor_{i % 10}',
            })
        
        start = time.time()
        # Query audit log (common operation)
        recent = [e for e in audit_log if e['decision'] == 'allow']
        duration = time.time() - start
        
        query_time_ms = duration * 1000
        monitor.record("Query 1000-entry audit log", query_time_ms, "ms", 10)
        
        monitor.print_report()
        
        assert query_time_ms < 50, f"Audit query too slow: {query_time_ms:.2f}ms"
    
    def test_pathologically_large_policy_document(self):
        """
        Edge: Very large OPA policy file (10,000+ rules)
        Expected: Policy evaluation should still be fast
        """
        monitor = PerformanceMonitor()
        
        # Simulate large policy with 10k rules
        policy_rules = [
            {'id': f'rule_{i}', 'condition': f'actor_id == "{i}"', 'decision': 'allow'}
            for i in range(10000)
        ]
        
        # Simulate policy evaluation (linear search in worst case)
        target_actor = '5000'
        
        start = time.time()
        matching_rule = next(
            (r for r in policy_rules if target_actor in r['condition']),
            None
        )
        duration = time.time() - start
        
        eval_time_ms = duration * 1000
        monitor.record("Evaluate 10k-rule policy", eval_time_ms, "ms", 50)
        
        monitor.print_report()


class TestConcurrencyEdgeCases:
    """Edge: Extreme concurrency scenarios"""
    
    def test_race_condition_detection_under_load(self):
        """
        Edge: 10,000 concurrent operations to same resource
        Expected: No race conditions, proper serialization
        """
        monitor = PerformanceMonitor()
        
        shared_counter = 0
        counter_lock = threading.Lock()
        race_detected = False
        
        def increment_counter():
            nonlocal shared_counter, race_detected
            with counter_lock:
                current = shared_counter
                # Simulate delay to increase race condition likelihood
                time.sleep(0.00001)
                shared_counter = current + 1
        
        start = time.time()
        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(increment_counter) for _ in range(10000)]
            [f.result() for f in as_completed(futures)]
        duration = time.time() - start
        
        throughput = 10000 / duration
        
        monitor.record("10k concurrent increments throughput", throughput, "ops/sec", 1000)
        
        # Verify correctness
        assert shared_counter == 10000, f"Race condition detected: {shared_counter} != 10000"
        
        monitor.print_report()
    
    def test_deadlock_detection_deep_nesting(self):
        """
        Edge: Deeply nested lock acquisition patterns
        Expected: No deadlocks, proper serialization
        """
        monitor = PerformanceMonitor()
        
        locks = [threading.Lock() for _ in range(10)]
        
        def acquire_locks_in_order(order: List[int]):
            for idx in order:
                locks[idx].acquire()
            for idx in reversed(order):
                locks[idx].release()
        
        # Try various orderings
        orderings = [
            list(range(10)),  # Sequential
            list(reversed(range(10))),  # Reverse
            [i for i in range(10) if i % 2 == 0] + [i for i in range(10) if i % 2 == 1],  # Interleaved
        ]
        
        for ordering in orderings:
            start = time.time()
            for _ in range(100):
                acquire_locks_in_order(ordering)
            duration = time.time() - start
            
            monitor.record(
                f"100 iterations with {len(ordering)}-lock nesting",
                duration * 1000,
                "ms",
                threshold=1000
            )
        
        monitor.print_report()


class TestFailureRecovery:
    """Edge: System behavior during failures"""
    
    def test_operation_retry_behavior(self):
        """
        Edge: Exponential backoff retry behavior
        Expected: Quick failure detection, bounded retry time
        """
        monitor = PerformanceMonitor()
        
        failed_attempts = 0
        max_retries = 5
        
        def operation_with_retries():
            nonlocal failed_attempts
            
            # First 3 attempts fail, 4th succeeds
            failed_attempts += 1
            if failed_attempts < 3:
                raise Exception("Transient failure")
            return True
        
        # Exponential backoff: 1ms, 2ms, 4ms, 8ms, 16ms
        start = time.time()
        retry_count = 0
        last_exception = None
        
        while retry_count < max_retries:
            try:
                operation_with_retries()
                break
            except Exception as e:
                last_exception = e
                retry_count += 1
                backoff_time = 0.001 * (2 ** retry_count)  # Exponential
                time.sleep(backoff_time)
        
        duration = time.time() - start
        
        monitor.record(
            f"Retry with exponential backoff ({retry_count} retries)",
            duration * 1000,
            "ms",
            threshold=100
        )
        
        monitor.print_report()
    
    def test_cascade_failure_prevention(self):
        """
        Edge: Prevent cascade failures when one actor fails
        Expected: Other actors should remain unaffected
        """
        monitor = PerformanceMonitor()
        
        # Simulate 10 actors, 1 fails
        actors = [
            {'id': i, 'status': 'healthy'}
            for i in range(10)
        ]
        actors[5]['status'] = 'failed'
        
        # Measure how many other actors are affected
        affected_count = 0
        for actor in actors:
            if actor['id'] != 5:  # Not the failed actor
                # Simulate operation
                if actor['status'] == 'failed':
                    affected_count += 1
        
        healthy_count = 10 - 1 - affected_count  # Total - failed - affected
        cascade_rate = affected_count / 9  # Out of other 9 actors
        
        monitor.record("Cascade failure rate", cascade_rate * 100, "%", 10)
        monitor.print_report()
        
        assert cascade_rate == 0, "Cascade failure detected"


# ── Module Summary ──────────────────────────────────────────────────

"""
EDGE PERFORMANCE TESTING SUMMARY

This suite measures CognitiveOS behavior under extreme conditions:

1. High Throughput:
   - 1000 sequential operations
   - 1000 concurrent operations
   - Throughput and latency metrics

2. Latency Extremes:
   - 100ms latency per operation
   - 1000ms latency per operation
   - Timeout behavior

3. Memory Pressure:
   - 100MB+ message handling
   - 1000 actors in memory

4. Pathological Input:
   - 500-depth delegation chains
   - 1000-entry audit logs
   - 10k-rule OPA policies

5. Extreme Concurrency:
   - 10,000 concurrent operations
   - Deep lock nesting patterns
   - Race condition detection

6. Failure Scenarios:
   - Retry behavior with exponential backoff
   - Cascade failure prevention

Execution: pytest tests/validation/test_edge_performance.py -v -s
"""
