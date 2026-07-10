# Stable Current CKB Download Path

## Goal

Keep the test framework's current CKB executable path stable across CKB upgrades. Running `python -m download` must make the final entry in `download.py`'s `versions` list available under `download/current`, so upgrading CKB no longer requires changing the current-node paths in `framework/test_node.py`.

## Scope

- Treat `versions[-1]` as the current CKB release.
- Continue downloading every configured version into its versioned directory.
- After all downloads succeed, replace `download/current` with a complete copy of the current release directory.
- Point `CURRENT_TEST`, `TESTNET`, `CURRENT_MAIN`, and `PREVIEW_DUMMY` at `download/current`.
- Preserve fixed paths such as `v200`, `v202`, and `v206` for compatibility tests.

Changing how versions are discovered, downloading only the newest version, and changing Docker image selection are outside this change.

## Design

`download.py` will expose a small synchronization function that accepts a source release directory and the destination `download/current`. If the destination already exists, the function removes it and copies the complete source directory in its place. Copying the complete release keeps `current` equivalent to a versioned download and avoids assumptions about which release files future tests may need.

The module-level download loop will move into a `main()` function guarded by `if __name__ == "__main__"`. `main()` will download the configured versions in order and synchronize `versions[-1]` only after every download completes. The existing rule that strips a prerelease suffix from the destination directory remains unchanged, so `0.208.0-rc0` resolves to `download/0.208.0`.

If the versions list is empty, synchronization will fail with a clear error instead of creating an undefined `current` directory. If any download or extraction fails, execution stops before `current` is replaced, leaving the prior working copy intact.

## Data Flow

1. `python -m download` calls `main()`.
2. `main()` downloads each entry in `versions` to its existing versioned destination.
3. It resolves the versioned directory for `versions[-1]`.
4. It replaces `download/current` with a full copy of that directory.
5. Current-node configurations read `ckb` and `ckb-cli` from `download/current`; compatibility configurations continue reading fixed version directories.

## Testing

Unit tests will use temporary directories and no network access. They will verify that synchronization:

- creates `current` with the source files;
- removes stale files from an existing `current` directory;
- preserves nested release content;
- configures all four current-node variants with `download/current` while leaving fixed-version variants unchanged.

The focused tests and the repository's relevant formatting or lint checks will be run before completion.
