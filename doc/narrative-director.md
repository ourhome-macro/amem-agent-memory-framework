# Runtime Governance

This project is domain-neutral, so it does not implement a mystery Narrative Director. The
equivalent governance layer is access and information-flow control:

- private memory remains private unless explicitly visible
- sensitive memory is blocked from normal context projection
- private memory cannot be promoted to shared/global through a later event
- replay detects rule/config drift
- source events remain the audit authority

Applications with stronger domain constraints should add pre-derivation and post-projection
policies around this runtime rather than letting agent text update memory directly.

