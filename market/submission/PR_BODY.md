- [x] Added one file: `data/plugins/shinerain1__dsh-project-2.yml`; generated READMEs and other entries are untouched.
- [x] Repository declares `dsh.bundle` with `cordis.patch.yml`.
- [x] Repository is at least one day old (created 2026-11-10 04:16:58 UTC).
- [x] Category is `workflow` and descriptions state implemented functionality without superlatives.
- [x] Repository has the `dsh-plugin` topic.

DSH Label Workbench provides durable text-annotation workflows when guidelines change. Its SQLite backend freezes rule and record versions, quarantines late job callbacks, preserves human review history, and checks completeness and provenance before exporting a dataset. DSH uses formal tools and can compose them through PTC; projects can be recovered by the host Session.

This is a local single-user early release. Python 3.11+ and the separately installed matching backend are required. Natural-language guideline changes require full review; this is not a claim of automatic semantic impact detection or improved labeling accuracy. Runtime state is managed through local host RPC, not claimed as DSH subprocess confinement.

The entry pins a prebuilt GitHub Release tarball. No install-time build script is required. Cordis and dsh-tools are host SDK peers; the schema utility is an ordinary runtime dependency.

- Installation: https://github.com/shinerain1/dsh-project-2/blob/main/docs/MARKETPLACE.md
- Public installation validation: https://github.com/shinerain1/dsh-project-2/blob/main/docs/publication-v0.1.0.json
- Code/tests and limits: https://github.com/shinerain1/dsh-project-2/blob/main/docs/VERIFICATION.md
- Real public data case: https://github.com/shinerain1/dsh-project-2/blob/main/docs/CFPB-CASE-STUDY.md
- Row-level audit and reproduction: https://github.com/shinerain1/dsh-project-2/tree/main/docs/cfpb

The CFPB case replays an actual product-taxonomy split on 1,200 public complaints. Worker delays and human-review events are controlled, not claimed as CFPB production logs or real participants. The plugin and a guarded ordinary script both prevent the specified integrity failures. Full review increases processing compared with a simple category filter; no general cost advantage is claimed.
