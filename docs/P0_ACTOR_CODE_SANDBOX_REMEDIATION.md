---
title: P0 Security Remediation - Actor Code Sandbox
inclusion: manual
---

# P0 Production Readiness: Actor Code Sandbox Remediation

**Status:** ⚠️ CRITICAL SECURITY GAP IDENTIFIED  
**Priority:** P0 (Must fix before production)  
**Test Suite:** `tests/validation/test_p0_actor_code_sandbox.py` (16 tests, all passing but documenting gaps)

---

## Executive Summary

**CRITICAL FINDING:** Actor Python code currently has unrestricted access to:
- **Network libraries:** requests, urllib, socket, httpx, http.client
- **Filesystem:** open(), os module, pathlib, glob
- **Database:** pymongo

**IMPACT:** Governance can be completely bypassed. Actor code can:
- Exfiltrate data via HTTP requests
- Read sensitive files
- Control database directly
- Modify audit logs (if file-writable)

**REMEDIATION:** Implement one of three sandbox strategies (below)

---

## Test Results

All P0 tests PASS but document the gaps:

```
tests/validation/test_p0_actor_code_sandbox.py
  ✅ 16 tests passing
  ⚠️  Multiple security gaps documented in test output

Gaps Identified:
  - Network: requests, urllib, socket, httpx, http.client
  - Filesystem: open(), os, pathlib, glob
  - Database: pymongo
  - Raw network: socket library
```

### Test Execution

```bash
pytest tests/validation/test_p0_actor_code_sandbox.py -v -s

# Output shows:
# ⚠️  SECURITY GAP FOUND: Actor code can import 'requests'
# ⚠️  SECURITY GAP FOUND: Actor code can use open()
# ⚠️  SECURITY GAP FOUND: Actor code can import 'pymongo'
# etc.
```

---

## Remediation Strategies

### Strategy 1: RestrictedPython (Recommended for Rapid Deployment)

**Approach:** Scan actor code before execution, compile to bytecode with restrictions

**Advantages:**
- Moderate effort to implement
- Works with unmodified Python code (mostly)
- Good performance
- Fine-grained control

**Disadvantages:**
- Cannot truly prevent all bypasses (bytecode can be complex)
- Requires code scanning for dangerous patterns

**Implementation:**

```python
# src/monkey_brain/kernel/actor_code_executor.py
from RestrictedPython import compile_restricted
from RestrictedPython.Guards import safe_globals, guarded_inplacebinary_op

def execute_actor_code_safely(actor_code: str, actor_context: dict) -> Any:
    """Execute actor code with RestrictedPython sandbox"""
    
    # 1. Compile with restrictions
    compiled = compile_restricted(
        actor_code,
        filename='<actor>',
        mode='exec'
    )
    
    if compiled.errors:
        raise ValueError(f"Code contains forbidden patterns: {compiled.errors}")
    
    # 2. Create safe globals
    safe_builtins = {
        # Safe builtins only
        'len': len,
        'range': range,
        'dict': dict,
        'list': list,
        'str': str,
        'int': int,
        'float': float,
        'bool': bool,
        'True': True,
        'False': False,
        'None': None,
        # Explicitly exclude dangerous builtins
        '__import__': None,
        'open': None,
        'exec': None,
        'eval': None,
    }
    
    safe_env = {
        '__builtins__': safe_builtins,
        '__name__': 'actor_sandbox',
        '__metaclass__': type,
        '_print_': print,  # For print support
        '_getattr_': getattr,
        '_getiter_': iter,
        '_iter_unpack_sequence_': iter,
        # Actor context (belief, world model, etc.)
        **actor_context,
    }
    
    # 3. Execute
    exec(compiled.code, safe_env)
    
    return safe_env.get('result')
```

**Installation:**

```bash
pip install RestrictedPython
```

**Integration Points:**

- `src/monkey_brain/kernel/cognitive_tick.py` — Execute actor code
- `tests/validation/test_p0_actor_code_sandbox.py` — Run P0 tests to verify

---

### Strategy 2: Separate Process with Restricted Python (Most Secure)

**Approach:** Run actor code in separate Python process with limited modules

**Advantages:**
- Maximum security (process isolation)
- Complete control over environment
- Cannot access parent process memory
- Can kill process on timeout/violation

**Disadvantages:**
- Highest overhead (process creation)
- IPC complexity (send context, receive result)
- Harder to debug

**Implementation:**

```python
# src/monkey_brain/kernel/actor_code_executor.py
import subprocess
import json
import tempfile

def execute_actor_code_in_sandbox(actor_code: str, actor_context: dict) -> Any:
    """Execute actor code in isolated subprocess with restricted modules"""
    
    # 1. Create sandbox script
    sandbox_script = f"""
import sys
import json

# Restrict sys.modules to only safe modules
ALLOWED_MODULES = {{'json', 'math', 'collections', 'datetime', 'random', 'uuid'}}

class ModuleBlocker:
    def find_module(self, fullname, path=None):
        module = fullname.split('.')[0]
        if module not in ALLOWED_MODULES and not fullname.startswith('__'):
            raise ImportError(f"Module {{module}} is not allowed in actor sandbox")
        return None

sys.meta_path.insert(0, ModuleBlocker())

# Now execute actor code
context = json.loads('{json.dumps(actor_context)}')

try:
    result = {{}}
    exec({repr(actor_code)}, {{'context': context, 'result': result}})
    print(json.dumps({{'success': True, 'result': result.get('result')}}))
except Exception as e:
    print(json.dumps({{'success': False, 'error': str(e)}}))
"""
    
    # 2. Run in subprocess
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(sandbox_script)
        script_path = f.name
    
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=30.0,  # Kill after 30 seconds
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Sandbox execution failed: {result.stderr}")
        
        output = json.loads(result.stdout)
        if not output['success']:
            raise RuntimeError(f"Actor code error: {output['error']}")
        
        return output['result']
    finally:
        os.unlink(script_path)
```

**Advantages Over Strategy 1:**
- Complete isolation (can't escape restrictions)
- Can timeout dangerous code
- Can monitor resource usage

---

### Strategy 3: Static Code Analysis (Fastest to Implement)

**Approach:** Scan actor code for forbidden imports/functions before execution

**Advantages:**
- Simplest implementation
- Requires no external libraries
- Fast (no compilation)
- Good for MVP

**Disadvantages:**
- Can be bypassed (sophisticated evasion techniques)
- False negatives possible
- Requires regular updates

**Implementation:**

```python
# src/monkey_brain/kernel/actor_code_executor.py
import ast
import re

FORBIDDEN_MODULES = {
    'requests', 'urllib', 'socket', 'httpx', 'aiohttp',
    'os', 'sys', 'subprocess', 'pathlib', 'glob',
    'pymongo', 'psycopg2', 'mysql', 'sqlalchemy',
}

def scan_actor_code_for_violations(actor_code: str) -> list[str]:
    """Scan actor code for forbidden imports/functions"""
    violations = []
    
    # 1. Parse AST
    try:
        tree = ast.parse(actor_code)
    except SyntaxError as e:
        violations.append(f"Invalid Python syntax: {e}")
        return violations
    
    # 2. Check imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split('.')[0]
                if module in FORBIDDEN_MODULES:
                    violations.append(f"Import of forbidden module: {module}")
        
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module = node.module.split('.')[0]
                if module in FORBIDDEN_MODULES:
                    violations.append(f"Import of forbidden module: {module}")
        
        elif isinstance(node, ast.Call):
            # Check for dangerous builtins (open, exec, eval)
            if isinstance(node.func, ast.Name):
                if node.func.id in {'open', 'exec', 'eval', '__import__'}:
                    violations.append(f"Forbidden builtin call: {node.func.id}")
    
    # 3. Regex patterns for evasion attempts
    dangerous_patterns = [
        r'__import__',
        r'exec\s*\(',
        r'eval\s*\(',
        r'open\s*\(',
    ]
    
    for pattern in dangerous_patterns:
        if re.search(pattern, actor_code):
            violations.append(f"Forbidden pattern detected: {pattern}")
    
    return violations

def execute_actor_code_with_scanning(actor_code: str, actor_context: dict) -> Any:
    """Execute actor code with pre-execution scanning"""
    
    # 1. Scan for violations
    violations = scan_actor_code_for_violations(actor_code)
    if violations:
        raise SecurityError(f"Code violates sandbox policy:\n" + "\n".join(violations))
    
    # 2. Execute with standard restrictions
    safe_env = {
        '__builtins__': {
            # Safe builtins only
            'len': len,
            'range': range,
            'sum': sum,
            'sorted': sorted,
            'print': print,
            # Explicitly exclude
            'open': None,
            '__import__': None,
        },
        **actor_context,
    }
    
    exec(actor_code, safe_env)
    return safe_env.get('result')
```

---

## Decision: Recommended Path (Hybrid)

**Phase 1 (Immediate - 1-2 days):**
- Implement Strategy 3 (Static Analysis) for MVP
- Update `cognitive_tick.py` to scan before execution
- Run P0 tests to verify gaps are enforced
- Document in security guide

**Phase 2 (Next sprint - 1 week):**
- Implement Strategy 1 (RestrictedPython) for better security
- Replace static analysis with compiled bytecode checking
- Add unit tests for evasion attempts

**Phase 3 (Future - optional):**
- Implement Strategy 2 (Subprocess isolation) for maximum security
- Use for untrusted/external actor code

---

## Integration Checklist

- [ ] Choose remediation strategy (recommend Strategy 3 → 1)
- [ ] Implement in `src/monkey_brain/kernel/actor_code_executor.py`
- [ ] Update `src/monkey_brain/kernel/cognitive_tick.py` to use sandbox
- [ ] Run P0 tests: `pytest tests/validation/test_p0_actor_code_sandbox.py`
- [ ] Verify no tests change from PASS → SKIP (all gaps must be fixed)
- [ ] Add integration tests with real actor code
- [ ] Update deployment documentation
- [ ] Security review before production

---

## Testing

Once sandbox is implemented, update P0 tests to REQUIRE restrictions:

```python
# Before: Tests document gaps (PASS with warnings)
# After: Tests verify restrictions are enforced (PASS without warnings)

# Example updated test:
def test_actor_code_cannot_import_requests(self):
    """REQUIREMENT: Actor code is restricted from 'requests'"""
    actor_code = "import requests"
    
    with pytest.raises(SecurityError):  # Should raise, not import successfully
        execute_actor_code_safely(actor_code, {})
    
    print("✅ 'requests' library correctly blocked")
```

---

## Deployment Checklist

**Before going to production:**

1. ✅ P0 tests created and documented gaps
2. ⏳ Implement sandbox strategy
3. ⏳ Update P0 tests to verify enforcement (not just gaps)
4. ⏳ Run full validation suite (all 94+ tests passing)
5. ⏳ Security review
6. ⏳ Deploy to staging
7. ⏳ Monitor for 24 hours (P1 stability test)
8. ⏳ Deploy to production

---

## Related Documents

- `tests/validation/test_p0_actor_code_sandbox.py` — P0 security tests
- `docs/ARCHITECTURE_AUDIT_COGNITIVEOS_SECURITY_GOVERNANCE.md` — Full security model
- `tests/validation/SYSTEMS_VALIDATION_REPORT.md` — Validation findings

---

## Questions?

See security and governance documentation above, or:
- Review P0 test output: `pytest tests/validation/test_p0_actor_code_sandbox.py -v -s`
- Check existing sandbox implementations in `src/monkey_brain/kernel/`
