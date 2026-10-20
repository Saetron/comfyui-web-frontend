# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.0] - 2026-10-20

### 🌸 Added
- **Playful Anime Aesthetic**:
  - Cosmic midnight twilight theme with soft sakura pink and neon lavender glowing cards.
  - Playful kaomoji accents `(✿◠‿◠)`, bouncy buttons, cute Japanese subtitles, and custom candy sliders.
  - Collapsible responsive hamburger drawer for mobile, tablet, and compact window views.
- **Danbooru Tagging Engine**:
  - Embedded dictionary of 140,000+ Danbooru tags with live post frequency counts and aliases.
  - Sub-millisecond typeahead autocomplete attached to positive and negative prompt inputs.
  - Color-coded category badges: General (sky), Character (emerald), Copyright (purple), Artist (rose), and Meta (amber).
  - Keyboard weighting shortcuts (`Ctrl+Up` / `Ctrl+Down` or `Cmd+Up` / `Cmd+Down`) adjusting tag emphasis (`(tag:1.1)`).
  - Quick tag palette drawers for Quality, Subject, Style, Lighting, and Negative staples.
  - Prompt formatting utilities: `space ➔ _`, `_ ➔ space`, deduplication, and automatic newline-to-comma replacement.
- **Workflow Discovery & Drag-and-Drop**:
  - Instant drag-and-drop import for ComfyUI `.json` workflow files and generated `.png` images.
  - Server workflow discovery covering ComfyUI `/userdata`, `/v2/userdata`, and PySSSSS `/pysssss/workflows`.
  - Local filesystem workflow persistence in `appdata/workflows/`, `config/`, and `data/`.
  - Intelligent latent tracing supporting standard KSamplers, SDXL conditioning, and custom nodes.
- **Gallery TikTok / Reel Mode & Detailed Inspection**:
  - Fullscreen randomized vertical slideshow with mouse wheel, swipe gestures, and keyboard navigation.
  - 5-second auto-play slideshow timer with progress bar and glass caption overlay.
  - Sequential `← Prev` and `Next →` navigation in detailed image modal with high-res zoom.
  - Clickable Danbooru prompt tag pills in image inspector for rapid re-use.
  - Internal styled confirmation modal replacing native browser `alert()`/`confirm()` dialogs.
- **Batch Queueing & Anime Summoning**:
  - Batch count slider (1–100) generating variation seeds per queue request.
  - Animated summoning circle during active generation.

### ⚡ Performance & Reliability
- **Prefix Trie Tag Indexer**: Replaced sequential scans with an in-memory prefix trie, accelerating Danbooru search by over 50x.
- **Gallery Crawling & LRU Cache**: Implemented multi-threaded directory walking with LRU caching for PNG metadata parsing, eliminating disk I/O bottlenecks.
- **HTTP Connection Pooling**: Shared persistent `httpx.AsyncClient` pool with concurrent probing of ComfyUI server workflow endpoints.
- **Non-blocking API Routes**: Converted synchronous filesystem and parsing operations to asynchronous background threads.
- **Frontend DOM Virtualization**: Batched DOM fragment rendering in gallery views and eliminated memory/timer leaks in WebSocket reconnection logic.

### 🐛 Fixed
- Resolved header overflow on small laptop and portrait screens using dynamic window resize observers.
- Elevated delete confirmation modal z-index to render cleanly above fullscreen popout images.
- Synchronized gallery item deletion in real-time across multiple connected browser sessions via WebSockets.
- Prevented double prompt queueing and event bubbling on generation controls.

---

## [1.0.0] - 2026-10-17

### 🚀 Initial Release
- **Lightweight ComfyUI Frontend**: Zero-build FastAPI web application styled with Tailwind CSS via CDN.
- **Persistent Filesystem Gallery**: Direct `/app/output` directory scanning and live updates via `watchfiles`.
- **WebSocket Streaming**: Real-time step progress, queue status, and node execution tracking.
- **Generator Controls**: Prompts, seeds, sampling steps, CFG scale, samplers, schedulers, and checkpoint selection.
- **Dockerized Deployment**: Multi-arch (amd64/arm64) Docker container published to GitHub Container Registry (`ghcr.io/saetron/comfyui-web-frontend`).
