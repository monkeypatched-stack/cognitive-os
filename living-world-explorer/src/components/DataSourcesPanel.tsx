import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { PanelContainer } from './PanelContainer'
import { useWorldStore } from '../store/worldStore'
import { useRefreshStore } from '../store/refreshStore'
import { useAuthStore } from '../store/authStore'
import { fetchAllActors } from '../api/actorClient'
import { fetchActorCognitiveState, type ActorCognitiveState } from '../api/cognitiveClient'
import {
  fetchExecutionPlanningContext, fetchExecutionSemanticMemory, fetchActorAffiliationChain, fetchExecutionConversation,
  type ContextSnapshotDto, type SemanticMemoryDto, type AffiliationChainNode, type ExecutionConversationMessageDto,
} from '../api/contextClient'
import { GroundingDebugger } from './GroundingDebugger'
import { ErrorBoundary } from './ErrorBoundary'
import { ExecutionsOverview } from './ExecutionsOverview'
import { DEMO_SNAPSHOT, DEMO_SEMANTIC_MEMORY, DEMO_META } from '../api/demoGroundingData'
import { ExecutionGraphPanel } from './ExecutionGraphPanel'
import { ConversationTimelinePanel } from './ConversationTimelinePanel'
import { MapPanel } from './MapPanel'
import { OntologyExplorerPanel } from './OntologyExplorerPanel'
import { SittingFacePanel } from './SittingFacePanel'
import { SecurityPanel } from './SecurityPanel'
import { OrdersWalletPanel } from './OrdersWalletPanel'
import { ProvidersPanel } from './ProvidersPanel'
import { GroundingGraphPanel } from './GroundingGraphPanel'
import { CapabilityAgentGraphPanel } from './CapabilityAgentGraphPanel'
import { InspectorPanel } from './InspectorPanel'
import { ExecutionHistoryPanel } from './ExecutionHistoryPanel'
import { MemoryPanel } from './MemoryPanel'
import { PlanReplacementHistoryPanel } from './PlanReplacementHistoryPanel'
import { PlanningDetailsPanel } from './PlanningDetailsPanel'
import { AgentsPanel } from './AgentsPanel'
import { SocietiesPanel } from './SocietiesPanel'
import { GeographyPanel } from './GeographyPanel'
import { EventStreamPanel } from './EventStreamPanel'
import { AffiliationsPanel } from './AffiliationsPanel'
import { RequestTimelinePanel } from './RequestTimelinePanel'
import { PlanAnalyzerPanel } from './PlanAnalyzerPanel'
import { CommunicationPanel } from './CommunicationPanel'
import { LemonMetricsPanel } from './LemonMetricsPanel'
import { NegotiationsPanel } from './NegotiationsPanel'
import { KnowledgeGraphPanel } from './KnowledgeGraphPanel'
import { ContextMemoryPanel } from './ContextMemoryPanel'
import './InspectorPanel.css'

const NAV_ROUTES: Record<string, string> = {
  Dashboard: '/dashboard', Actors: '/actors', Societies: '/societies', 'World Map': '/world-map', Timeline: '/timeline',
  Debugger: '/execution-debugger/grounding', 'Plan Analyzer': '/plan-analyzer',
  Negotiations: '/negotiations', 'Knowledge Graph': '/knowledge-graph', 'Grounding Graph': '/grounding-graph', 'Context Stream': '/context-stream',
  Memories: '/memories', Affiliations: '/affiliations', 'Lemon Metrics': '/lemon-metrics', Providers: '/provider-registry', Capabilities: '/providers',
  Communication: '/communication', Security: '/security', 'Orders & Wallet': '/orders-wallet', Settings: '/settings',
}
const TAB_ROUTES: Record<string, string> = { Plan: 'plan', Grounding: 'grounding', Conversations: 'conversations', Metrics: 'metrics', Logs: 'logs' }

// Root `/` and `/dashboard` are the CognitiveOS home surface; the same
// GroundingDebugger content under Execution Debugger (`/execution-debugger`,
// any `.../grounding`) is the Debugger surface. Both render the same
// live evidence component, but the chrome (nav highlight, breadcrumb,
// page title) must identify which one is actually showing.
function navForPath(path: string): string {
  if (path === '/' || path === '/dashboard') return 'Dashboard'
  if (path === '/execution-debugger' || path.endsWith('/grounding')) return 'Debugger'
  if (path === '/agents') return 'Actors'
  const navMatch = Object.entries(NAV_ROUTES).find(([, route]) => route === path || (route !== '/' && path.startsWith(route)))
  return navMatch?.[0] ?? 'Dashboard'
}

function DashboardFrame({ dashboard, children, onExport, debuggerPage = true }: { dashboard: boolean; children: ReactNode; onExport?: () => void; debuggerPage?: boolean }) {
  const navigate = useNavigate()
  const location = useLocation()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const initials = (user?.email ?? '??').slice(0, 2).toUpperCase()
  const [activeNav, setActiveNav] = useState(() => navForPath(location.pathname))
  const [activeTab, setActiveTab] = useState('Grounding')
  useEffect(() => {
    const path = location.pathname
    setActiveNav(navForPath(path))
    const tabMatch = Object.entries(TAB_ROUTES).find(([, slug]) => path.endsWith(`/${slug}`))
    if (tabMatch) setActiveTab(tabMatch[0])
  }, [location.pathname])
  const nav = (label: string) => { setActiveNav(label); navigate(NAV_ROUTES[label] ?? '/') }
  const tab = (label: string) => { setActiveTab(label); navigate(`/execution-debugger/${TAB_ROUTES[label]}`) }
  const dashboardHome = activeNav === 'Dashboard'
  if (!dashboard) return <PanelContainer title="Data Sources">{children}</PanelContainer>
  return <div className="lwe-dashboard">
    <aside className="lwe-dashboard-sidebar">
      <div className="lwe-dashboard-logo"><span>⚛</span> CognitiveOS <span className="lwe-dashboard-chevron">⌄</span></div>
      <div className="lwe-dashboard-nav-label">OVERVIEW</div>
      {['⌂  Dashboard', '♙  Actors', '♧  Societies', '◉  World Map', '▣  Timeline', '¤  Orders & Wallet'].map((item) => <button key={item} type="button" onClick={() => nav(item.slice(3))} className={`lwe-dashboard-nav-item${activeNav === item.slice(3) ? ' active' : ''}`}>{item}</button>)}
      <div className="lwe-dashboard-nav-label">ANALYSIS</div>
      <button type="button" onClick={() => nav('Debugger')} className={`lwe-dashboard-nav-item${activeNav === 'Debugger' ? ' active' : ''}`}>▧  Debugger</button>
      {['⌘  Plan Analyzer', '◌  Negotiations'].map((item) => <button key={item} type="button" onClick={() => nav(item.slice(3))} className={`lwe-dashboard-nav-item${activeNav === item.slice(3) ? ' active' : ''}`}>{item}</button>)}
      <div className="lwe-dashboard-nav-label">DATA</div>
      {['⌘  Knowledge Graph', '◈  Grounding Graph', '▤  Context Stream', '◉  Memories', '♧  Affiliations'].map((item) => <button key={item} type="button" onClick={() => nav(item.slice(3))} className={`lwe-dashboard-nav-item${activeNav === item.slice(3) ? ' active' : ''}`}>{item}</button>)}
      <div className="lwe-dashboard-nav-label">SYSTEM</div>
      {['⛓  Providers', '◉  Capabilities', '☎  Communication', '⛨  Security', '⌁  Lemon Metrics', '⚙  Settings'].map((item) => <button key={item} type="button" onClick={() => nav(item.slice(3))} className={`lwe-dashboard-nav-item${activeNav === item.slice(3) ? ' active' : ''}`}>{item}</button>)}
    </aside>
    <main className="lwe-dashboard-main">
      <header className="lwe-dashboard-header">
        <div className="lwe-dashboard-breadcrumb">Living World Explorer <b>›</b> {dashboardHome ? activeNav : debuggerPage ? 'Debugger' : activeNav}</div>
        <div className="lwe-dashboard-header-actions"><div className="lwe-dashboard-search">⌕ &nbsp; Search anything...</div><span>◎</span><span>♧</span><span className="lwe-dashboard-avatar">{initials}</span><span className="lwe-dashboard-user">{user?.email ?? 'Unknown user'}<small>{user?.role || 'User'}</small></span><button type="button" className="lwe-dashboard-logout" onClick={logout} title="Sign out">⏻</button></div>
      </header>
      <div className="lwe-dashboard-pagehead"><div><h1>{dashboardHome ? 'CognitiveOS Dashboard' : debuggerPage ? 'Execution Debugger' : activeNav} <span>{dashboardHome ? 'Live' : debuggerPage ? 'Completed' : 'Live'}</span></h1><p>{dashboardHome ? 'Living world operations and cognitive execution overview' : debuggerPage ? 'Grounding view · persisted planner context and execution evidence' : `CognitiveOS ${activeNav.toLowerCase()} workspace`}</p></div>{debuggerPage && <><button type="button" onClick={() => tab('Grounding')}>Compare</button><button type="button" onClick={onExport}>Export Report⌄</button></>}</div>
      {debuggerPage && <nav className="lwe-dashboard-tabs">{['Plan', 'Grounding', 'Conversations', 'Metrics', 'Logs'].map((item) => <button type="button" key={item} onClick={() => tab(item)} className={activeTab === item ? 'selected' : ''}>{item === 'Grounding' ? '▧' : '◉'} &nbsp; {item}</button>)}</nav>}
      {children}
    </main>
  </div>
}

function DashboardPage({ title, description }: { title: string; description: string }) {
  return <div className="lwe-dashboard-placeholder-page">
    <div className="lwe-dashboard-placeholder-icon">◈</div>
    <h2>{title}</h2>
    <p>{description}</p>
    <div className="lwe-dashboard-placeholder-grid"><div>Live data source</div><div>Execution-aware view</div><div>Coming online with the selected actor</div></div>
  </div>
}

/**
 * A peer of World Tree / Map / Execution Graph / Inspector — not a
 * sub-section buried inside another panel's scroll. Whichever actor is
 * selected (World Tree "BY ACTOR" tab, or the Map), this panel shows what
 * grounded their most recent real planning cycle: Knowledge Graph, Context
 * Stream, Semantic Memory, Affiliation Graph, World State, and External
 * Events — automatically, with no click required beyond selecting the
 * actor.
 */
export function DataSourcesPanel({ dashboard = false }: { dashboard?: boolean }) {
  const selectedActorId = useWorldStore((s) => s.selectedActorId)
  const refreshSeq = useRefreshStore((s) => s.refreshSeq)
  const location = useLocation()
  const navigate = useNavigate()

  const [cognitiveState, setCognitiveState] = useState<ActorCognitiveState | null>(null)
  const [executionId, setExecutionId] = useState<string | null>(null)
  const [snapshot, setSnapshot] = useState<ContextSnapshotDto | null>(null)
  const [semanticMemory, setSemanticMemory] = useState<SemanticMemoryDto | null>(null)
  const [executionMessages, setExecutionMessages] = useState<ExecutionConversationMessageDto[]>([])
  const [affiliationChain, setAffiliationChain] = useState<AffiliationChainNode[]>([])
  const [error, setError] = useState('')
  const lastActorIdRef = useRef<string | null>(null)

  // Dashboard is the full CognitiveOS home surface; Grounding is the same
  // live evidence surface under Execution Debugger. Other routes are
  // intentionally independent page shells.
  const groundingRoute = location.pathname === '/' || location.pathname === '/dashboard' || location.pathname === '/execution-debugger' || location.pathname.endsWith('/grounding')
  // The Debugger's tabs (Overview/Plan/Grounding/...) and Compare/Export
  // actions belong to the Execution Debugger surface, not the Dashboard
  // home surface, even though both render this same grounding content.
  const isDashboardHome = location.pathname === '/' || location.pathname === '/dashboard'

  useEffect(() => {
    if (!dashboard || selectedActorId) return
    let cancelled = false
    fetchAllActors().then((actors) => {
      if (cancelled || actors.length === 0) return
      // The dashboard's default actor list, so it needs one row per real
      // actor: /planet/actors (fetchActors) duplicates a row per society
      // membership (see actorClient.ts) and would make actors[0]
      // unpredictable. actors[0] can also be an orphaned/throwaway test
      // actor with no goals or execution history; prefer one that's
      // actually doing something, falling back to the first entry.
      //
      // Priya Sharma is the deliberate primary demo actor (scripts/
      // seed_world.py prints "Primary demo actor: Priya Sharma" for
      // exactly this reason) — the "first actor with any goals" fallback
      // below is order-dependent and picked Arjun Mehta instead whenever
      // he happened to come back before Priya in /actors, even though
      // both have goals seeded. Name a real preference instead of
      // leaving the dashboard's default actor to incidental API order.
      const priya = actors.find((a) => a.name === 'Priya Sharma')
      const withGoals = actors.find((a) => (a.goals?.length ?? 0) > 0)
      useWorldStore.getState().selectActor((priya ?? withGoals ?? actors[0]).actor_id)
    }).catch(() => { /* the normal panel error state will explain API failures */ })
    return () => { cancelled = true }
  }, [dashboard, selectedActorId])

  useEffect(() => {
    const switchingActor = lastActorIdRef.current !== selectedActorId
    lastActorIdRef.current = selectedActorId
    if (switchingActor) {
      setCognitiveState(null)
      setExecutionId(null)
      setSnapshot(null)
      setSemanticMemory(null)
      setExecutionMessages([])
      setAffiliationChain([])
      setError('')
    }
    if (!selectedActorId) return
    let cancelled = false
    fetchActorCognitiveState(selectedActorId)
      .then((result) => {
        if (cancelled) return
        setCognitiveState(result)
        // A page like OrdersWalletPanel's "View execution trace" sets
        // this to open the Debugger already scoped to a specific
        // execution — consumed once, then cleared, so it never overrides
        // a later, ordinary visit (which should keep defaulting to the
        // most recent execution below).
        const requested = useWorldStore.getState().selectedExecutionId
        if (requested && result.execution_history.some((e) => e.metadata.execution_id === requested)) {
          setExecutionId(requested)
          useWorldStore.getState().selectExecution(null)
          return
        }
        const latest = result.execution_history.find(
          (e) => typeof e.metadata.execution_id === 'string' && e.metadata.execution_id,
        )
        setExecutionId(latest ? (latest.metadata.execution_id as string) : null)
      })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)) })
    // Actor-scoped, not execution-scoped — a real traversal of this
    // actor's own affiliation graph (see fetchActorAffiliationChain),
    // independent of which execution is currently selected.
    fetchActorAffiliationChain(selectedActorId).then((chain) => {
      if (!cancelled) setAffiliationChain(chain)
    }).catch(() => { /* the Affiliation Graph card falls back to snapshot.affiliations */ })
    return () => { cancelled = true }
  }, [selectedActorId, refreshSeq])

  useEffect(() => {
    if (!selectedActorId || !executionId) {
      setSnapshot(null)
      setSemanticMemory(null)
      setExecutionMessages([])
      return
    }
    let cancelled = false
    Promise.all([
      fetchExecutionPlanningContext(selectedActorId, executionId),
      fetchExecutionSemanticMemory(selectedActorId, executionId),
      fetchExecutionConversation(selectedActorId, executionId),
    ])
      .then(([snap, memory, messages]) => {
        if (cancelled) return
        // Snapshots written before the grounding schema was extended do not
        // contain the newer arrays. Normalize at the API boundary so the
        // panel remains readable while those executions age out.
        setSnapshot({
          ...snap,
          affiliations: snap.affiliations ?? [],
          available_resources: snap.available_resources ?? [],
          retrieval_sources: snap.retrieval_sources ?? [],
          retrieval_latency_ms: snap.retrieval_latency_ms ?? {},
          relevant_locations: snap.relevant_locations ?? [],
          relevant_objects: snap.relevant_objects ?? [],
          available_capabilities: snap.available_capabilities ?? [],
          summary: snap.summary ?? { entity_count: 0, relationship_count: 0, context_event_count: 0, experience_count: 0 },
        })
        setSemanticMemory({
          ...memory,
          retrieved_this_execution: memory.retrieved_this_execution ?? { experiences: [], conversations: [] },
          durable_beliefs: memory.durable_beliefs ?? [],
        })
        setExecutionMessages(messages ?? [])
        setError('')
      })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)) })
    return () => { cancelled = true }
  }, [selectedActorId, executionId])

  if (dashboard && !groundingRoute) {
    const pageTitle = location.pathname.split('/').filter(Boolean).map((part) => part.replace(/-/g, ' ')).map((part) => part.replace(/^./, (c) => c.toUpperCase())).join(' · ')
    if (location.pathname === '/execution-debugger/demo') {
      // A dedicated, clearly-labeled demo execution — never mixed into a
      // real actor's data. Fed entirely from demoGroundingData.ts, no
      // live fetch, no dependency on selectedActorId, so it renders even
      // with no actor selected and never touches real API state.
      // Same chrome as the real Debugger (title, tabs, Compare/Export) —
      // it's meant to be indistinguishable from the real experience,
      // just backed by a fixture instead of a live fetch.
      const exportDemoSnapshot = () => {
        const blob = new Blob([JSON.stringify(DEMO_SNAPSHOT, null, 2)], { type: 'application/json' })
        const url = URL.createObjectURL(blob)
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = `execution-grounding-${DEMO_SNAPSHOT.execution_id}.json`
        anchor.click()
        URL.revokeObjectURL(url)
      }
      return <DashboardFrame dashboard onExport={exportDemoSnapshot}>
        <div className="lwe-inspector" style={{ display: 'block', background: 'transparent' }}>
          <GroundingDebugger
            snapshot={DEMO_SNAPSHOT}
            semanticMemory={DEMO_SEMANTIC_MEMORY}
            executionMessages={[]}
            meta={DEMO_META}
            onViewGraph={() => navigate('/knowledge-graph')}
            onViewAffiliations={() => navigate('/affiliations')}
          />
        </div>
      </DashboardFrame>
    }
    if (location.pathname === '/execution-debugger/plan') {
      return <DashboardFrame dashboard>
        <div className="lwe-plan-page"><div className="lwe-plan-stack"><PlanningDetailsPanel /><ExecutionGraphPanel /><PlanReplacementHistoryPanel /></div></div>
      </DashboardFrame>
    }
    if (location.pathname === '/execution-debugger/conversations') {
      return <DashboardFrame dashboard>
        <div className="lwe-plan-page"><ConversationTimelinePanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/world-map') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page"><div className="lwe-world-impact-stack"><GeographyPanel /><MapPanel /></div></div>
      </DashboardFrame>
    }
    if (location.pathname === '/timeline') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><RequestTimelinePanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/affiliations') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page"><AffiliationsPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/knowledge-graph') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><OntologyExplorerPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/grounding-graph') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><GroundingGraphPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/providers') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><CapabilityAgentGraphPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/plan-analyzer') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><PlanAnalyzerPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/negotiations') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><NegotiationsPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/communication') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><CommunicationPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/settings') {
      return <DashboardFrame dashboard debuggerPage={false}><div className="lwe-plan-page" /></DashboardFrame>
    }
    if (location.pathname === '/lemon-metrics') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><LemonMetricsPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/security') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><SecurityPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/orders-wallet') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><OrdersWalletPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/provider-registry') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><ProvidersPanel /></div>
      </DashboardFrame>
    }
    // No nav button links here anymore (that slot is now Grounding Graph
    // above), but the real operational "World State Graph" and the earlier
    // SittingFace knowledge-graph explorer both stay reachable directly at
    // /world-state and /sitting-face rather than being deleted.
    if (location.pathname === '/world-state') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}><KnowledgeGraphPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/sitting-face') {
      return <DashboardFrame dashboard debuggerPage={false}>
        <div className="lwe-plan-page"><SittingFacePanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/execution-debugger/metrics') {
      return <DashboardFrame dashboard>
        <div className="lwe-plan-page"><InspectorPanel includeExecutionHistory={false} includeMemory={false} includePlanReplacementHistory={false} includePlanningSummary={false} includeSocietyView={false} /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/execution-debugger/memories') {
      return <DashboardFrame dashboard>
        <div className="lwe-plan-page"><MemoryPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/execution-debugger/logs') {
      return <DashboardFrame dashboard>
        <div className="lwe-plan-page"><ExecutionHistoryPanel /></div>
      </DashboardFrame>
    }
    if (location.pathname === '/actors' || location.pathname === '/agents') {
      return <DashboardFrame dashboard debuggerPage={false}><div className="lwe-plan-page"><AgentsPanel /></div></DashboardFrame>
    }
    if (location.pathname === '/societies') {
      return <DashboardFrame dashboard debuggerPage={false}><div className="lwe-plan-page"><SocietiesPanel /></div></DashboardFrame>
    }
    if (location.pathname === '/context-stream') {
      return <DashboardFrame dashboard debuggerPage={false}><div className="lwe-plan-page"><EventStreamPanel /></div></DashboardFrame>
    }
    if (location.pathname === '/memories') {
      return <DashboardFrame dashboard debuggerPage={false}><div className="lwe-plan-page"><ContextMemoryPanel /></div></DashboardFrame>
    }
    return <DashboardFrame dashboard>
      <DashboardPage title={pageTitle || 'Dashboard'} description="This is an independent dashboard page. Its live data view is scoped to the selected actor and execution." />
    </DashboardFrame>
  }

  if (!selectedActorId) {
    return <DashboardFrame dashboard={dashboard} debuggerPage={!isDashboardHome}>
      <div className="lwe-inspector-muted" style={{ padding: '12px' }}>
        Select an actor (World Tree — BY ACTOR, or the Map) to see what grounded their most recent planning cycle.
      </div>
    </DashboardFrame>
  }

  const name = cognitiveState?.identity.name ?? selectedActorId

  // The Dashboard home surface is an overview across every real execution
  // this actor has ever run, not a drill-down into whichever one happens
  // to be "latest" — that per-execution view is what the Execution
  // Debugger's Grounding tab is for. Clicking a row there sends you into
  // the Debugger already scoped to that execution.
  if (isDashboardHome) {
    return <DashboardFrame dashboard={dashboard} debuggerPage={false}>
      {error && <div className="lwe-inspector-error" style={{ padding: '12px' }}>{error}</div>}
      <div style={{ padding: '12px' }}>
        <ExecutionsOverview
          cognitiveState={cognitiveState}
          actorName={name}
          onOpenExecution={(execId) => { setExecutionId(execId); navigate('/execution-debugger/grounding') }}
        />
      </div>
    </DashboardFrame>
  }

  const exportSnapshot = () => {
    if (!snapshot) return
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `execution-grounding-${snapshot.execution_id}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  // The real ExecutionHistoryEntry matching this snapshot's execution_id
  // (when the actor's history has aged past it, the snapshot's own
  // fields are still enough to render a real header — nothing here is
  // fabricated, just sourced from whichever real record is available).
  const executionEntry = cognitiveState?.execution_history.find((e) => e.metadata.execution_id === executionId)

  return <DashboardFrame dashboard={dashboard} onExport={exportSnapshot}><div className="lwe-inspector" style={{ display: 'block', background: 'transparent', padding: dashboard ? undefined : '12px' }}>
    {!executionId && !error && <div className="lwe-inspector-muted">No executions recorded for this actor yet.</div>}
    {error && <div className="lwe-inspector-error">{error}</div>}

    {snapshot && (
      <ErrorBoundary label="Execution Debugger" compact>
        <GroundingDebugger
          snapshot={snapshot}
          semanticMemory={semanticMemory}
          executionMessages={executionMessages}
          meta={{
            executionId: snapshot.execution_id,
            actorName: name,
            goal: executionEntry?.goal ?? '',
            status: executionEntry?.outcome ?? 'unknown',
            time: snapshot.created_at,
            failureReason: executionEntry?.failure_reason || undefined,
            stepFailures: executionEntry?.step_failures,
            affiliationChain: affiliationChain.length > 0 ? affiliationChain : undefined,
          }}
          onViewGraph={() => navigate('/knowledge-graph')}
          onViewAffiliations={() => navigate('/affiliations')}
        />
      </ErrorBoundary>
    )}
  </div></DashboardFrame>
}
