# Production secrets: Vault + External Secrets Operator

Replaces `deploy/k8s/secret.yaml`'s checked-in placeholder credential
values with a Secret synced live from HashiCorp Vault, via the
[External Secrets Operator](https://external-secrets.io/) (ESO). Nothing
else about the base manifests changes — every Deployment keeps reading a
Secret named `agentos-secrets` with the same 9 keys it does today.

`deploy/k8s/secret.yaml` (applied directly, or via the root
`deploy/k8s/kustomization.yaml`) remains the path for local/dev/kind
clusters that don't run Vault or ESO. This overlay is the production
path.

## One-time cluster setup (outside this repo)

1. **Install External Secrets Operator** (e.g. via its Helm chart) —
   provides the `SecretStore`/`ExternalSecret` CRDs and controller this
   overlay's manifests depend on.

2. **Enable Vault's Kubernetes auth method**, trusting this cluster:

   ```
   vault auth enable kubernetes
   vault write auth/kubernetes/config \
     kubernetes_host="https://<cluster-api-server>"
   ```

3. **Write a read-only policy** scoped to this one secret path:

   ```
   vault policy write agentos-production - <<'EOF'
   path "secret/data/monkeybrain/production/agentos-secrets" {
     capabilities = ["read"]
   }
   EOF
   ```

4. **Bind that policy to the ServiceAccount** this overlay creates
   (`external-secrets-vault-auth` in the `monkeybrain` namespace):

   ```
   vault write auth/kubernetes/role/agentos-production \
     bound_service_account_names=external-secrets-vault-auth \
     bound_service_account_namespaces=monkeybrain \
     policies=agentos-production \
     ttl=1h
   ```

5. **Write the real secret values into Vault** (never into git — this
   replaces editing `deploy/k8s/secret.yaml` by hand):

   ```
   vault kv put secret/monkeybrain/production/agentos-secrets \
     neo4j-password=... \
     neo4j-auth=... \
     internal-service-token=... \
     access-token-secret=... \
     refresh-token-secret=... \
     agentos-api-key=... \
     openrouter-api-key=... \
     moss-project-id=... \
     moss-project-key=...
   ```

6. **Point `vault-secretstore.yaml` at the real Vault address** — edit
   its placeholder `spec.provider.vault.server` value.

## Apply

```
kubectl apply -k deploy/k8s/overlays/production/ --load-restrictor=LoadRestrictionsNone
```

`kubectl get externalsecret agentos-secrets -n monkeybrain` should reach
`SecretSynced` once ESO has pulled from Vault; `kubectl get secret
agentos-secrets -n monkeybrain -o yaml` should show the same 9 keys
`deploy/k8s/secret.yaml` has today, sourced from Vault instead.

## Rotation

Vault-side rotation (`vault kv put` a new value) propagates automatically
within `refreshInterval` (1h, set in `external-secret.yaml`) — no
`kubectl apply` needed. A Deployment still needs a rolling restart to
pick up a changed env var value from the resulting Secret (unchanged
from today's behavior; this overlay doesn't add anything like Reloader).

## Not covered here

This overlay was written and validated with `kubectl kustomize` locally
(renders cleanly) but **not exercised against a real Vault + ESO
installation** — there is no such cluster available from this
environment to verify against. Confirm the auth flow (step 2-4 above)
end-to-end in a real cluster before relying on it for a production
deploy.
