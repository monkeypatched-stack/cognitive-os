"use client";

// Goals view for a selected actor — GET /actors (fetchAllActors) for the
// picker, GET /actors/{id}/goals (fetchActorGoals) for the list. Both are
// real, already-live backend endpoints living-world-explorer already uses.
import { useEffect, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import { fetchActorGoals, fetchAllActors, type Actor } from "../../lib/actorClient";

function GoalsContent() {
  const [actors, setActors] = useState<Actor[]>([]);
  const [selectedActorId, setSelectedActorId] = useState("");
  const [goals, setGoals] = useState<string[]>([]);
  const [loadingActors, setLoadingActors] = useState(false);
  const [loadingGoals, setLoadingGoals] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoadingActors(true);
    fetchAllActors()
      .then((result) => {
        if (cancelled) return;
        setActors(result);
        if (result.length > 0) setSelectedActorId(result[0].actor_id);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingActors(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedActorId) {
      setGoals([]);
      return;
    }
    let cancelled = false;
    setLoadingGoals(true);
    setError("");
    fetchActorGoals(selectedActorId)
      .then((result) => {
        if (!cancelled) setGoals(result.goals);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingGoals(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedActorId]);

  return (
    <div className="page">
      <h1>Goals</h1>
      {loadingActors && <div className="empty-state">Loading actors…</div>}
      {!loadingActors && actors.length === 0 && !error && <div className="empty-state">No actors found.</div>}
      {!loadingActors && actors.length > 0 && (
        <select
          className="actor-picker"
          value={selectedActorId}
          onChange={(e) => setSelectedActorId(e.target.value)}
        >
          {actors.map((actor) => (
            <option key={actor.actor_id} value={actor.actor_id}>
              {actor.name || actor.actor_id}
            </option>
          ))}
        </select>
      )}
      {loadingGoals && <div className="empty-state">Loading goals…</div>}
      {!loadingGoals && error && <div className="empty-state">{error}</div>}
      {!loadingGoals && !error && selectedActorId && goals.length === 0 && (
        <div className="empty-state">No goals recorded for this actor.</div>
      )}
      {!loadingGoals && !error && goals.length > 0 && (
        <ul>
          {goals.map((goal, i) => (
            <li key={`${goal}-${i}`}>{goal}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function GoalsPage() {
  return (
    <RequireAuth>
      <GoalsContent />
    </RequireAuth>
  );
}
