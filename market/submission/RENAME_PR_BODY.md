Rename the existing annotation-workbench entry from `shinerain1/dsh-project-2` to `shinerain1/dsh-label-workbench`, keeping the author prefix.

The author renamed the GitHub repository. This is the same repository (GitHub ID `1452193799`, created `2026-11-10T04:16:58Z`) already accepted in #14174, not a second plugin or new listing.

- Rename the YAML file to match the repository slug.
- Update `url`, `name` and the pinned GitHub Release `tarball` URL.
- Keep the existing description, category, implementation and release version unchanged.
- Do not modify generated READMEs or any other plugin entry.

Validation: the current entry validator passes with exactly one annotation-workbench entry. Downloading the v0.1.0 asset through the new repository URL returns the same SHA-256: `4c5f18c45434c9cac2b6eccb3719bfea0a2da14e9beace7f700acb6de82cf6da`. GitHub's old repository API path redirects to the renamed repository with the same ID.

New repository: https://github.com/shinerain1/dsh-label-workbench
