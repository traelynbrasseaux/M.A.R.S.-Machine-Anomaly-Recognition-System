// Single-port 16-bit RAM with a registered read, shaped like one iCE40 SPRAM block.
// Used for simulation and for the Tang Nano 20K, where yosys maps it to block RAM.
`default_nettype none

module spram #(
    parameter DEPTH = 16384,
    parameter AW = 14
) (
    input  wire          clk,
    input  wire          we,
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [AW-1:0] addr,
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire [15:0]   wdata,
    output reg  [15:0]   rdata
);
  reg [15:0] mem[0:DEPTH-1];

  // Read data holds during a write, so nothing may rely on it in the cycle after one.
  // The address port stays AW wide when DEPTH is smaller, like the block it stands in for.
  /* verilator lint_off WIDTHTRUNC */
  always @(posedge clk) begin
    if (we) mem[addr] <= wdata;
    else rdata <= mem[addr];
  end
  /* verilator lint_on WIDTHTRUNC */
endmodule

`default_nettype wire
