2026-05-02 | Phase 1 project scaffold | Python 3.14 (uv venv), mss for screen capture, PostgreSQL 15+ schema with 5 tables, FastAPI skeleton
2026-05-02 | Schema design | UUID primary keys, JSONB extensibility on all tables, positional mapping via button index, 6-max/9-max seat order
2026-05-02 | Screen capture approach | Using mss (not claude-vision MCP directly in Python) — mss provides fast native capture; MCP vision tools will be used for OCR/classification in Phase 2
2026-05-02 | Phase 2: OCR + card recognition | EasyOCR (lightweight, ~50MB) chosen over PaddleOCR (~500MB); card recognition is dual-path (moondream + color/OCR heuristic); moondream unavailable on Python 3.14 (pillow build fails), heuristic is the active path
2026-05-02 | Phase 2: Storage layer | Sync SQLAlchemy with psycopg2; DSN password URL-encoded (%40 for @); ORM models match schema.sql exactly
2026-05-02 | Phase 2: State tracking | imagehash.average_hash for hero card change detection; string equality for action text diffs; community card count for street progression
2026-05-03 | Python downgrade 3.14→3.13.13 | 3.14 too new (Pillow build fails, moondream incompatible); 3.13 is fully stable with all wheels
2026-05-03 | Vision model: moondream→SmolVLM-256M | moondream 1.2 requires API key even for local inference; SmolVLM-256M is Apache 2.0, 256M params, <1GB VRAM, free; HF mirror (hf-mirror.com) for China network access
