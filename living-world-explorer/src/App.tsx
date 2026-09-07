import { useEffect } from 'react'
import { RouterProvider } from 'react-router-dom'
import { router } from './routes/router'
import { useThemeStore } from './theme/themeStore'
import { useWebSocket } from './api/useWebSocket'
import { useApiLiveness } from './api/useApiLiveness'
import { ErrorBoundary } from './components/ErrorBoundary'
import { fetchAllActors } from './api/actorClient'
import { useWorldStore } from './store/worldStore'
import { useAuthStore } from './store/authStore'

function App() {
  const mode = useThemeStore((s) => s.mode)
  const selectedActorId = useWorldStore((s) => s.selectedActorId)
  const authStatus = useAuthStore((s) => s.status)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', mode)
  }, [mode])

  // App-wide default actor, not just the Dashboard route: whichever
  // panel the user lands on first (Chat, World Tree, ...) should already
  // have Priya Sharma selected rather than requiring a manual pick.
  // Same preference DataSourcesPanel.tsx's own dashboard-scoped effect
  // uses (Priya Sharma is the deliberate primary demo actor per scripts/
  // seed_world.py) — duplicated rather than shared here because both
  // effects already guard on `if (selectedActorId) return`, so whichever
  // runs first wins and the other becomes a harmless no-op.
  useEffect(() => {
    // Every real call goes through apiClient.request(), which requires a
    // Bearer token now that the cluster enforces real auth (see
    // authStore.ts) — firing this before login completes just produced a
    // guaranteed, silently-caught 401 on every app boot. Re-runs once
    // authStatus flips to 'authenticated' (RequireAuth's redirect means
    // this effect's owning component is mounted well before that point,
    // on the login page itself).
    if (selectedActorId || authStatus !== 'authenticated') return
    let cancelled = false
    fetchAllActors().then((actors) => {
      if (cancelled || actors.length === 0) return
      const priya = actors.find((a) => a.name === 'Priya Sharma')
      const withGoals = actors.find((a) => (a.goals?.length ?? 0) > 0)
      useWorldStore.getState().selectActor((priya ?? withGoals ?? actors[0]).actor_id)
    }).catch(() => { /* panels that need an actor already show their own error state */ })
    return () => { cancelled = true }
  }, [selectedActorId, authStatus])

  // Both called once, at the root: connection state lives in
  // connectionStore, not local component state, so any panel can read
  // it later without these hooks being re-invoked per panel.
  useWebSocket()
  useApiLiveness()

  // Last line of defense: every route renders through AppShell (router.tsx),
  // so a render error anywhere in the tree would otherwise blank the whole
  // page with no recovery — exactly what you don't want mid-demo. A more
  // specific boundary also wraps the Execution Debugger itself
  // (DataSourcesPanel.tsx), the most complex and most frequently-changing
  // surface, so a bad execution's data doesn't take the whole shell down.
  return (
    <ErrorBoundary label="Living World Explorer">
      <RouterProvider router={router} />
    </ErrorBoundary>
  )
}

export default App
