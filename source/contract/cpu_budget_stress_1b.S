# One finite VM invocation: 1 billion current_cycles syscalls.
# Only two debug messages; no loop logging or sleeping.
.option norvc
.section .text
.globl _start
_start:
    la a0, begin_message
    li a7, 2177
    ecall
    li t0, 1000000000
    li a7, 2042
1:
    ecall
    addi t0, t0, -1
    bnez t0, 1b
    la a0, end_message
    li a7, 2177
    ecall
    li a0, 0
    li a7, 93
    ecall
.section .rodata
begin_message: .asciz "CPU_STRESS_BEGIN_1B"
end_message: .asciz "CPU_STRESS_END_1B"
