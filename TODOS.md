# TODOS

## Pre-Production

### Solace HA/Clustering
- **What:** Configure Solace HA pair or migrate to Solace Cloud managed service
- **Why:** Single Solace broker is a single point of failure. Broker down = entire pipeline halts.
- **Context:** Architecture docs acknowledge this gap. Staging runs single broker. Production needs HA before go-live.
- **Depends on:** Phase 4 (K8s manifests) complete

### PAIR API Resilience
- **What:** Add health monitoring, distinguish 'no results' from 'API unreachable', implement fallback to curated vector store with UX indicator
- **Why:** PAIR Search API was discovered via network inspection (unofficial, reverse-engineered). Failures silently produce empty results, masquerading as "no precedents found."
- **Context:** PAIR covers higher courts only. SCT/traffic decisions already use vector store. Build resilience into search_precedents tool during Phase 4a.
- **Depends on:** Phase 4a (search_precedents tool)

## Future Scaling

### Redis Key Sharding for Layer2Aggregator
- **What:** Implement key sharding or migrate to Redis Cluster for the aggregator
- **Why:** Current design uses per-case Redis keys with Lua scripts. At 500+ concurrent cases, Redis single-thread serialization could bottleneck.
- **Context:** At projected 50-200 cases/month, this is a non-issue. Only matters at high concurrent volume.
- **Depends on:** Phase 3 (Layer2Aggregator) complete + production usage data
