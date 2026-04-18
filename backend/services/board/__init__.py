"""
Universal Live-Board Engine (multi-sport)
==========================================
Central home for the sport-agnostic board lifecycle:

    services.board.adapters  — per-sport adapters (NBA, MLB, …)
    services.board.reader    — single read path `get_board(sport, tier, limit)`
    services.board.scanner   — 60s universal game-start scanner

The engine owns orchestration and state. Adapters own sport meaning (scoring,
tier classification, canonical identity, game-start extraction).

Adding a new sport = implement `SportBoardAdapter` + register. Zero changes
to the engine.
"""
