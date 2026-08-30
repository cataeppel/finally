# FinAlly Project - the Finance Ally

All project documentation is in the `planning` directory:

- `planning/PLAN.md` — the product specification. Included in full below.
- `planning/MARKET_DATA.md` — **the summary of the market data backend**
  (`backend/app/market/`): architecture, the simulator model, the Massive path, the wire
  contract, tests, and what is still missing. Read this before touching anything under
  `backend/`.
- `planning/MARKET_DATA_DESIGN.md`, `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`,
  `MASSIVE_API.md` — the long-form design documents the module was built from. Reference
  material: full derivations, the Massive endpoint reference, original code listings. They
  predate the implementation, so where they disagree with `MARKET_DATA.md` or the code, the
  code wins — `MARKET_DATA.md` §13 lists what changed and why.

To see the market data backend actually running:
`cd backend && uv run python scripts/market_data_demo.py` (add `--check` for a self-test).

The key document is PLAN.md included in full here:

@planning/PLAN.md
