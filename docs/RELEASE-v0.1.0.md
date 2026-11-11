# DSH Label Workbench 0.1.0

Early community release for local single-label text annotation workflows in DeepSeek Harness.

- Immutable rule and record versions, durable jobs/leases and idempotent callbacks.
- Stale-result isolation, preservation of human review history and completeness-gated provenance exports.
- 17 DSH tools with PTC composition and Session-linked recovery.
- Local review page and Label Studio JSON exchange; no bundled Label Studio server.

Install the Python wheel separately, then add the prebuilt `.tgz` through DSH and configure the Python executable. Python 3.11+, Node 24.9+; tested on Windows with Python 3.12 and DSH 0.2.2-alpha.3. See [installation instructions](https://github.com/shinerain1/dsh-project-2/blob/main/docs/MARKETPLACE.md).

The backend uses the Python standard library only. The release has no install-time code/build hook. Checksums are supplied in SHA256SUMS-v0.1.0.txt.

Evidence includes fault-injection tests, a small live DSH Agent comparison, actual DSH PTC checks and a replay of an official CFPB taxonomy change on 1,200 real public complaints. These support workflow integrity, not better semantic annotation accuracy or reduced review cost. Properly guarded ordinary scripts also pass. Natural-language rule changes require full review.

This is an unofficial single-user tool, not production certification, multi-user authorization or an official DeepSeek endorsement. Human review events in automated benchmarks are simulated. Browser visual acceptance and broader platform testing remain incomplete.
