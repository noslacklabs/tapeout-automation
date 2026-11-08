module chipflow_probe(input probe_clock, input reset_high, output reg [3:0] value);
  always @(posedge probe_clock)
    if (reset_high) value <= 4'b0000;
    else value <= value + 1'b1;
endmodule
