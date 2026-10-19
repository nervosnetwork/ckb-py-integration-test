# Test location

Executable tests belong under `test_cases/`, not `suites/`. Follow this project
layout even when a generic test-authoring workflow suggests a `suites/` directory.

For network-verification regression tests, use
[`../test_cases/network_verification/README.md`](../test_cases/network_verification/README.md).
The runner is `test_cases/network_verification/run_selected.py`; reusable
helpers and fixtures live beside those tests. Existing DAO, Type ID, capacity,
VM and sync tests remain in their original `test_cases/` groups.

This directory is only a navigation reminder. Do not add executable tests or
copy test implementations here. Keep case contracts in `reviews/` and runtime
results in the ignored `report/` directory. Directory changes must not change
test expectations or the repository's default CKB test version.
