// S8: a bit-serial 8-bit CPU for SCL's 1.2 um C1D process. "S8" is a working name.
// PRIVATE: don't publish.
//
// The reference is spec/isa.md. If this file and the spec disagree, this file is wrong.
//
// How it works, in short
// ----------------------
// The registers PC, ACC and B are rings: on every clock, each one rotates one place to
// the right, so bit 0 of the value passes the right-hand end once every 8 clocks. A
// register that is left alone comes back to where it started, so it needs no selector
// to hold its value. To change a register, we feed different bits into the left-hand
// end while its bits go past.
//
// Time is counted in ticks (one clock), slots (8 ticks, one byte) and frames (5 slots,
// one RAM access). Ring position always matches the tick counter: when t = 0, bit 0 of
// each ring is at the right-hand end, so serial maths starts from the lowest bit.
//
//   Frame:  IDLE      CMD       HI        LO        DAT
//   Ticks:  0-7       8-15      16-23     24-31     32-39
//
// An instruction is 3 frames: FETCH1 reads the opcode, FETCH2 reads the operand into B,
// and EXEC does the work. All flip-flops use the rising edge of clk. The one exception
// to "everything is a flip-flop output" is SCK, which is a gated copy of the clock.

module s8(
  input        clk,
  input        rst,    // Active high. Hold for 16 rising edges. See spec section 7.
  input        miso,   // Data from the RAM.
  input  [2:0] gpi,    // Pins IN0-IN2. Only jump instructions read them.
  output       sck,    // RAM clock.
  output reg   cs_n,   // RAM select, active low. Pin CS.
  output       mosi,   // Data to the RAM.
  output reg [2:0] gpo // Pins OUT0-OUT2.
);

  // Reset is read through one flip-flop, so RST can change at any moment relative to clk.
  // Everything below uses rs, never rst.
  reg rs;
  always @(posedge clk) rs <= rst;

  // ------------------------------------------------------------------- Time
  reg [2:0] t;    // Tick within the slot, 0 to 7. Free-running.
  (* fsm_encoding = "none" *) reg [1:0] slot;  // 0 CMD, 1 HI, 2 LO, 3 DAT. Stays 0 during IDLE.
  reg       idle; // 1 during the IDLE slot. Stuck at 1 once halted.

  // Which frame of the instruction this is. Advances at the end of the DAT slot.
  localparam FETCH1 = 2'd0, FETCH2 = 2'd1, EXEC = 2'd2, HALTED = 2'd3;
  (* fsm_encoding = "none" *) reg [1:0] frame;

  wire t_first = ~|t;              // t == 0
  wire t_last  = &t;               // t == 7
  wire in_lo   = slot[1] & ~slot[0];
  wire in_dat  = &slot;            // Can't be true in IDLE, where slot is 0.
  wire in_exec = frame[1];         // EXEC or HALTED. No slot work happens when halted.
  wire halted  = &frame;
  wire exec_dat = in_exec & in_dat;

  // ------------------------------------------------------------------- Registers
  reg [7:0] pc;   // Program counter ring.
  reg [7:0] acc;  // Accumulator ring.
  reg [7:0] b;    // Operand ring, loaded in FETCH2.
  reg [4:0] ir;   // Top 5 bits of the opcode byte: {class[1:0], f[2:0]}. Doesn't rotate.
  reg       c;    // Carry flag.
  reg       z;    // Zero flag.
  reg       pcc;  // Two jobs. In fetch frames: the carry while PC adds 1.
                  // In EXEC: the jump decision, or the old C for ROR.

  // ------------------------------------------------------------------- Decode
  wire [2:0] f       = ir[2:0];
  wire       is_imm  = ir[3];                   // Class 1: the value comes from B, not MISO.
  wire       is_alu  = ~ir[4] & ~(f == 3'd6);   // Classes 0 and 1, except ST and CPI.
  wire       is_st   = ~ir[4] & ~ir[3] & (f == 3'd6);
  wire       is_cpi  = ~ir[4] &  ir[3] & (f == 3'd6);
  wire       sets_flags = is_alu | is_cpi;
  wire       is_jump =  ir[4] & ~ir[3];
  wire       is_misc =  ir[4] &  ir[3];
  wire       is_out  = is_misc & (f == 3'd1);
  wire       is_ror  = is_misc & (f == 3'd2);
  wire       is_hlt  = is_misc & (f == 3'd3);
  wire       f_add   = (f == 3'd1);             // ADD, ADI
  wire       f_sub   = (f == 3'd2) | is_cpi;    // SUB, SUI, CPI
  wire       f_adc   = (f == 3'd7);             // ADC, ACI
  wire       writing = in_exec & is_st;         // This frame is a RAM WRITE.

  // The decision, read once per tick during IDLE, so the value at the end of tick 7 of
  // EXEC is the one that counts. A pin changing later can't touch PC.
  // Note: ROR has f = 2, which selects c here. That's how ROR saves the old C in pcc.
  reg cond;
  always @* case (f)
    3'd0: cond = 1'b1;     // JMP
    3'd1: cond = z;        // JZ
    3'd2: cond = c;        // JC  (and ROR)
    3'd3: cond = ~z;       // JNZ
    3'd4: cond = gpi[0];   // JI0
    3'd5: cond = gpi[1];   // JI1
    3'd6: cond = gpi[2];   // JI2
    3'd7: cond = ~c;       // JNC
  endcase

  // ------------------------------------------------------------------- One-bit ALU
  wire a     = acc[0];
  wire val   = is_imm ? b[0] : miso;
  wire val2  = f_sub ? ~val : val;
  wire sum   = a ^ val2 ^ c;
  wire carry = (a & val2) | (c & (a ^ val2));
  reg  res;
  always @* case (f)
    3'd0:    res = val;      // LD, LDI
    3'd3:    res = a & val;  // AND, ANI
    3'd4:    res = a | val;  // OR, ORI
    3'd5:    res = a ^ val;  // XOR, XRI
    default: res = sum;      // ADD, SUB, ADC and immediates; CPI (f = 6) uses only the flags.
  endcase

  // ------------------------------------------------------------------- Clocked state
  // In reset, the rings and IR take in zeros, so 8 clocks of reset clear them. That's why
  // RST must stay high for a while, and why no bit of the chip stays unknown.
  wire miso_g  = miso & ~rs;
  wire load_ir = rs | ((frame == FETCH1) & in_dat);
  wire load_b  = rs | ((frame == FETCH2) & in_dat);

  always @(posedge clk) begin
    t <= rs ? 3'd0 : t + 3'd1;

    if (idle)        slot <= 2'd0;
    else if (t_last) slot <= slot + 2'd1;

    if (rs)          idle <= 1'b1;
    else if (t_last) idle <= in_dat | (idle & halted);  // Enter IDLE after DAT; leave at its end.

    if (rs)                    frame <= FETCH1;
    else if (t_last & in_dat)  frame <= in_exec ? (is_hlt ? HALTED : FETCH1) : frame + 2'd1;

    // CS rises one tick into IDLE, so it stays low 1 clock past the last SCK pulse,
    // and falls exactly at the start of CMD.
    // The last term changes nothing after a reset, when CS high always means idle. Before
    // the first reset, the flip-flops are random: this term, and slot clearing itself during
    // idle above, make sure CS can only fall at the true start of a frame. Otherwise random
    // bits could reach the RAM as a command, such as EQIO, which S8's reset can't undo.
    cs_n <= rs | (idle & (~t_last | halted)) | (cs_n & ~idle);

    // PC ring. Adds 1 during LO of each fetch frame, or takes B during DAT of a taken jump.
    pc <= { ~rs & ((exec_dat & is_jump & pcc) ? b[0]
                                              : pc[0] ^ (pcc & ~in_exec & in_lo)),
            pc[7:1] };

    if (idle)                 pcc <= ~in_exec | cond;
    else if (~in_exec & in_lo) pcc <= pcc & pc[0];

    b <= { load_b ? miso_g : b[0], b[7:1] };

    if (load_ir) ir <= { miso_g, ir[4:1] };

    // ACC ring. ROR takes bit 1 for the first 7 ticks, skipping old bit 0, then the old C.
    acc <= { ~rs & ( (exec_dat & is_alu) ? res
                   : (exec_dat & is_ror) ? (t_last ? pcc : acc[1])
                   :                       a ),
             acc[7:1] };

    if (rs) begin
      c   <= 1'b0;
      z   <= 1'b1;
      gpo <= 3'd0;
    end else begin
      if (idle & in_exec & sets_flags) begin  // Get ready for the serial sum.
        z <= 1'b1;
        if (f_add) c <= 1'b0;
        if (f_sub) c <= 1'b1;
      end
      if (exec_dat & sets_flags) begin
        if (res) z <= 1'b0;
        if (f_add | f_sub | f_adc) c <= carry;
      end
      if (exec_dat & is_ror & t_first) c   <= a;         // Old bit 0 goes to C.
      if (exec_dat & is_out & t_first) gpo <= acc[2:0];  // Ring is in place at t = 0.
    end
  end

  // ------------------------------------------------------------------- Pins
  // SCK is a gated clock: high only while clk is low, outside IDLE. idle changes just after
  // clk rises, when this output is already held low, so it can't glitch. The layout must
  // check that idle settles before clk falls.
  assign sck = ~(clk | idle);

  // Command byte, first bit first: 0000 0011 is READ, 0000 0010 is WRITE.
  wire cmd_bit = t[2] & t[1] & ~(t[0] & writing);

  assign mosi = ~idle & ( (slot == 2'd0) ? cmd_bit
                        : in_lo          ? (in_exec ? b[0] : pc[0])
                        :                  (in_dat & writing & a) );

`ifdef FORMAL
  `include "s8_props.vh"   // Proofs only: formal/s8_props.vh. Not part of the chip.
`endif

endmodule
