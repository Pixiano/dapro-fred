# God's Eye View (decision, 2026-10-11)

Source: https://github.com/bilawalsidhu/gods-eye-view (MIT, browser app: live aircraft, ships, satellites, earthquakes, fires, weather, traffic and public cameras on a 3D globe; Cesium + Vite; voice through the OpenAI Realtime API; Google 3D Tiles).

Vatsal's decision:
- It is a SEPARATE project, not part of Core and not a few borrowed parts: the whole thing, which FRED reaches as a subsystem (a tool / service FRED calls).
- Filed as Phase 9 of the build, or Mark II (his wording: "Mark 2 or Phase 9"); to be placed when the Mark II plan is next opened.
- Anything that needs a paid or cloud API gets a free alternative (voice: local speech and the local brain instead of OpenAI Realtime; globe imagery: a free tile source instead of Google 3D Tiles; feed keys: the free tiers only).
- NOT cloned now. Nothing is built or installed.

Open points for when it starts: which free imagery source replaces Google 3D Tiles; which data layers need keys; how FRED talks to it (local HTTP/MCP tool on localhost); GPU/browser load next to FRED's models.
