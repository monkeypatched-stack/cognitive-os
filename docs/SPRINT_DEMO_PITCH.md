# YC Fall 2026 x Moss: The Zero Latency Builder Sprint
## 2-Minute Demo & Presentation Playbook

**Target Score:** 95+ / 100  
**Judging Criteria Addressed:**
- Product and User Experience: **35%**
- Technical Execution: **30%**
- Speed and Latency: **20%**
- Demo and Presentation: **15%**

---

## ⏱️ 2-Minute Timed Pitch Script

### [0:00 – 0:25] The Hook & Problem Statement (Category: Product & Technical)
> *"Most multi-agent frameworks today are static prompt-chains that operate in a vacuum. But in the physical world—with drones, robots, and automated facilities—reality doesn't wait.*
> 
> *A 4-second LLM planning latency means a drone crashes or a robot stalls. Furthermore, if a cloud agent disconnects, its identity and state evaporate. We built **CognitiveOS**: an operating system for persistent autonomous actors coordinating in a governed world."*

---

### [0:25 – 0:55] Live Product & UX (Category: Product & UX — 35%)
> *(Switch to browser displaying `http://localhost:3000/drone-flight`)*
>
> *"Here is our live operator dashboard. Notice:*
> 1. *Each drone is an independent **Actor Cell** with isolated state, memory, and credentials.*
> 2. *They coordinate over a high-speed **NATS Society Bus** using local Knowledge Graph synchronization.*
> 3. *Above all, look at our top HUD: the **Zero Latency Engine powered by Moss**."*

---

### [0:55 – 1:35] The Zero-Latency Breakthrough with Moss (Category: Speed & Latency — 20%)
> *(Switch to terminal running `./scripts/run_mac_sprint.sh demo` or show the HUD delta)*
>
> *"Here is the central breakthrough of our architecture:*
> 
> *When an actor encounters a brand-new mission, it pays the standard cold LLM reasoning cost: **3,420 milliseconds**.*
>
> *Traditional caches use MD5 or SHA256 hashes—meaning if a mission is rephrased by even one word, the cache misses, and you are stuck waiting another 3.5 seconds.*
>
> *We integrated **Moss Semantic Plan Caching**. When we send a paraphrased goal—'inspect boundary zone Alpha' instead of 'patrol perimeter sector Alpha'—Moss performs vector similarity over the goal embeddings. It matches the verified plan and executes in **9.4 milliseconds**.*
> 
> *That is a **360x speedup**, turning a slow LLM deliberation into a **zero-latency real-time reaction**."*

---

### [1:35 – 2:00] Fail-Closed Governance & Wrap-Up (Category: Technical Execution — 30%)
> *"Finally, speed without safety in physical AI is catastrophic. In CognitiveOS, cognition is probabilistic, but execution is governed.*
>
> *If an actor attempts an unauthorized flight maneuver into restricted airspace, our in-memory **TransitionGate** evaluates policy and rejects the transition in **0.4 milliseconds**—fail-closed.*
>
> *CognitiveOS delivers persistent physical AI actors with zero-latency reaction speeds and deterministic safety boundaries. Thank you."*

---

## 🎯 Quick Judge Q&A Defense

| Anticipated Question | Confident Technical Answer |
|---|---|
| **"Why not just use an exact prompt cache (Redis/Memcached)?"** | *"Natural language goals are inherently non-deterministic. A user or upstream agent saying 'patrol perimeter' vs 'inspect border' misses any exact-string hash. Moss vector search matches the underlying semantic intent at sub-10ms latency."* |
| **"How do you prevent a cached plan from causing a collision or danger?"** | *"Plans are proposals, never authority. Every plan retrieved from Moss still passes through our TransitionGate and PlanValidator in <0.5ms before any actuator or capability can fire."* |
| **"Can this run on edge hardware (Jetson, Raspberry Pi, Mac)?"** | *"Yes! We validated CognitiveOS on both Kubernetes and native edge processes via our `EdgeAgent` supervisor. On an Apple Silicon Mac, in-memory state CAS exceeds 40,000 ops/sec."* |

---

## 🚀 Pre-Demo Checklist on Your Mac

1. Run `./scripts/run_mac_sprint.sh demo` once before presentation to ensure terminal colors and output are pre-cached.
2. Run `npm run dev` in `apps/workspace` and open `http://localhost:3000/drone-flight`.
3. Check that the Mac power cable is connected and `caffeinate` is active.
