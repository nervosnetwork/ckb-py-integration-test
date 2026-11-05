ENTRY(_start)
PHDRS { program PT_LOAD FLAGS(5); }
SECTIONS {
  . = 0x10000;
  .text : { *(.text) *(.rodata) } :program
  /DISCARD/ : { *(.comment) *(.riscv.attributes) }
}
