# VM test contract sources

The eight `current_cycles`, `exec_*` and `spawn_*` ELF files added for the
network-verification regressions have their corresponding C sources alongside
them. `spawn_utils.h` is their shared upstream helper. Sources and existing
ELFs are copied unchanged from CKB
[`c2596c9d2ea161afbeb9ba905b66a34454aaef27`, `script/testdata`](https://github.com/nervosnetwork/ckb/tree/c2596c9d2ea161afbeb9ba905b66a34454aaef27/script/testdata).
`vm-fixtures.json` records the source and ELF SHA-256 values, and `COPYING.ckb`
retains the upstream license. `cpu_budget_stress_1b` is maintained here as
`cpu_budget_stress_1b.S` plus its linker script.

## Edit and build

Install LLVM with the RISC-V target and LLD (validated with LLVM 18.1.8).
Run from this directory:

```sh
git clone https://github.com/nervosnetwork/ckb-c-stdlib.git tmp/ckb-c-stdlib
git -C tmp/ckb-c-stdlib checkout --detach 6bf2bca668e0db5f9eb120eb78b31782899ef90d
make -f Makefile.vm-fixtures
```

On macOS with Homebrew LLVM 18, pass
`RISCV_CLANG=/opt/homebrew/opt/llvm@18/bin/clang` to `make`.
An existing header checkout can be selected with `CKB_STDLIB=/absolute/path`.
The Makefile checks the dependency commit; changing that pin is an explicit
toolchain update. No CKB executable is built.

Build outputs go to `tmp/vm-fixtures/`; the checked-in ELFs stay untouched.
After editing a source, rerun `make` and inspect the corresponding output.
The C build uses the pinned headers above, not a recovered record of the
historical upstream compiler. Fresh C outputs can differ in bytes and VM cycles
from the imported ELFs. A successful compile establishes buildability, not
equivalent behavior or historical byte-for-byte reproducibility.

## Updating a regression input

Validate the changed contract through its real VM/transaction scenarios before
copying the output over the checked-in ELF. Review source and binary together.
Update the source/binary hashes in `vm-fixtures.json` and the binary hashes in
[`vm-cycle-baseline.json`](../../test_cases/network_verification/fixtures/vm-cycle-baseline.json).
If VM cycles change, obtain an independent reference and review the explicit
cycle expectations there and in the affected tests. Tests do not regenerate
these expected values from the binary under test.

Ordinary pytest runs use the checked-in inputs directly; no build, download,
budget override or compiler is added to their setup by this Makefile.
