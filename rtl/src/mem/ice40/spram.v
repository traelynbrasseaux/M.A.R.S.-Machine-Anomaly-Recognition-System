// iCE40 UltraPlus build of spram: one SB_SPRAM256KA block (16K x 16), so DEPTH <= 16384 and AW = 14.
`default_nettype none

module spram #(
    parameter DEPTH = 16384,
    parameter AW = 14
) (
    input  wire          clk,
    input  wire          we,
    input  wire [AW-1:0] addr,
    input  wire [15:0]   wdata,
    output wire [15:0]   rdata
);
  SB_SPRAM256KA ram (
      .ADDRESS(addr),
      .DATAIN(wdata),
      .MASKWREN(4'b1111),
      .WREN(we),
      .CHIPSELECT(1'b1),
      .CLOCK(clk),
      .STANDBY(1'b0),
      .SLEEP(1'b0),
      .POWEROFF(1'b1),  // active low
      .DATAOUT(rdata)
  );
endmodule

`default_nettype wire
