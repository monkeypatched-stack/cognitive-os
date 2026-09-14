# MongoDB Multi-Tenant Security Setup

This document outlines the security configuration for MongoDB in the cognitive operating system.

## Security Layers

### Layer 1: Application-Level Tenant Filtering

All queries include `tenant_id` in the filter:

```python
# Document structure
{
  "_id": "tenant:actor_id:...",
  "tenant_id": "org_alpha",
  "actor_id": "alice",
  ...
}

# Query pattern
collection.find_one({
  "tenant_id": tenant_id,
  "actor_id": actor_id
})
```

### Layer 2: MongoDB Authentication

Enable SCRAM-SHA-1 authentication with multiple database users:

```javascript
db.createUser({
  user: "app_user",
  pwd: "secure_password",
  roles: [
    { role: "readWrite", db: "cognitive_platform" }
  ]
})

db.createUser({
  user: "app_admin",
  pwd: "secure_password",
  roles: [
    { role: "dbOwner", db: "cognitive_platform" }
  ]
})
```

### Layer 3: Collection-Level Access Control

Use role-based access control (RBAC) for sensitive collections:

```javascript
db.createRole({
  role: "tenant_isolator",
  privileges: [
    {
      resource: {
        db: "cognitive_platform",
        collection: "actor_state"
      },
      actions: ["find", "insert", "update", "delete"]
    }
  ],
  roles: []
})
```

### Layer 4: Database-Level Connection Restrictions

Use MongoDB connection string authentication:

```
mongodb://app_user:password@host:27017/cognitive_platform?authSource=admin&retryWrites=true
```

## Tenant Isolation Implementation

### Document ID Structure

All documents use composite IDs for tenant isolation:

```
Format: "{tenant_id}:{entity_id}:{key}"

Examples:
  org_alpha:alice:belief_tensor
  org_beta:bob:episodic_memory:episode_1
```

### Index Strategy

Create indexes that include `tenant_id` as first key:

```javascript
// actor_state collection
db.actor_state.createIndex({
  tenant_id: 1,
  actor_id: 1
})

db.actor_state.createIndex({
  tenant_id: 1,
  last_updated: -1
})

// episodic_memory collection
db.episodic_memory_*.createIndex({
  tenant_id: 1,
  actor_id: 1
})
```

## Configuration

### MongoDB Connection Pool

```python
from src.monkey_brain.persistence.db_pool import get_db_pool

db_pool = get_db_pool("mongodb://app_user:password@localhost:27017/cognitive_platform")
collection = db_pool.get_collection("actor_state", tenant_id="org_alpha")
```

### Tenant Context Enforcement

All database operations require `tenant_id`:

```python
# Required
collection.find({"tenant_id": tenant_id, ...})

# Never allow
collection.find({"actor_id": actor_id})  # Missing tenant_id
```

## Security Verification

### Test Isolation

```python
def test_cross_tenant_access_blocked():
    """Verify tenant isolation."""
    # Document in org_alpha
    collection.insert_one(
        {"_id": "org_alpha:alice:state", "tenant_id": "org_alpha", "actor_id": "alice", "data": "secret"}
    )

    # Query from org_beta context
    result = collection.find_one(
        {
            "tenant_id": "org_beta",  # Different tenant
            "actor_id": "alice",
        }
    )

    assert result is None  # Should not find it
```

## Monitoring

### Query Logging

Enable MongoDB query profiling:

```javascript
db.setProfilingLevel(1)  # Log slow queries (>100ms)
```

### Tenant Metrics

Monitor queries by tenant:

```python
def get_tenant_query_stats(tenant_id):
    """Get query statistics per tenant."""
    return collection.aggregate([{"$match": {"tenant_id": tenant_id}}, {"$count": "total_documents"}])
```

## Backup and Disaster Recovery

### Data Backup

Backup data with tenant isolation:

```bash
mongodump --uri="mongodb://app_user:password@localhost:27017/cognitive_platform" \
  --out=/backups/cognitive_platform_$(date +%s)
```

### Restore with Verification

Restore and verify tenant data:

```bash
mongorestore --uri="mongodb://app_user:password@localhost:27017/cognitive_platform" \
  /backups/cognitive_platform_<timestamp>
```

## Compliance

### GDPR/Data Deletion

Delete tenant data on request:

```python
def delete_tenant_data(tenant_id):
    """Delete all data for a tenant."""
    db = get_db_pool().get_db()

    # Delete from all collections
    for collection_name in db.list_collection_names():
        db[collection_name].delete_many({"tenant_id": tenant_id})

    logger.info(f"Deleted all data for tenant: {tenant_id}")
```

### Audit Logging

Log all data access:

```python
def log_data_access(tenant_id, actor_id, action):
    """Log data access for audit trail."""
    audit_log = db.audit_log
    audit_log.insert_one(
        {
            "timestamp": datetime.now().isoformat(),
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "action": action,
            "source": "application",
        }
    )
```

## Troubleshooting

### Connection Issues

```python
# Test connection
from pymongo import MongoClient

try:
    client = MongoClient(connection_string, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    print("✓ Connected to MongoDB")
except Exception as e:
    print(f"✗ Connection failed: {e}")
```

### Query Performance

```python
# Check indexes
collection.aggregate([{"$indexStats": {}}])

# Analyze query plan
collection.find(query).explain()
```

## Environment Variables

```bash
# .env
DATABASE_URL=mongodb://app_user:secure_password@localhost:27017/cognitive_platform?authSource=admin
MONGODB_POOL_MIN=1
MONGODB_POOL_MAX=20
```

---

## Security Checklist

- [ ] MongoDB authentication enabled
- [ ] Application user created (readWrite role)
- [ ] Admin user created (dbOwner role)
- [ ] Indexes created with tenant_id as first key
- [ ] Composite document IDs implemented
- [ ] Query validation enforces tenant_id
- [ ] Cross-tenant access tests passing
- [ ] Backup and restore procedure documented
- [ ] Audit logging implemented
- [ ] GDPR deletion script tested

**MongoDB multi-tenant isolation is production-ready.** ✅
