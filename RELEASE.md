# Release Guide

1. Run tests:

```bash
pytest
```

2. Update the version in `pyproject.toml`.

3. Commit the release change:

```bash
git add pyproject.toml RELEASE.md
git commit -m "chore: release vX.Y.Z"
```

4. Create and push a tag:

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin main --tags
```

5. Create a GitHub release from the tag.
