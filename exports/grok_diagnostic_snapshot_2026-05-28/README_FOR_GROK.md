Current public snapshot commit hash: 3d7a7d5454196462306b027286a69ed6920289fe
Current branch name: consultant/full-snapshot-public-20260402T055838Z
This is the dirty tree snapshot for Grok on 2026-05-28

Routing note:
- code_aliases/wallet_guardian.py is an alias-only compatibility surface, not the rebuilt wallet owner logic
- the real wallet rebuild lives under prodesk/wallet/
- start with:
  - prodesk/wallet/guardian.py
  - prodesk/wallet/wallet_controller.py
  - prodesk/wallet/wallet_health.py
  - prodesk/wallet/wallet_provider.py
  - prodesk/wallet/wallet_types.py
- code_aliases/risk_engine.py is also alias-only; the real risk owner file is prodesk/risk.py
