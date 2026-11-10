// Tang Nano 20K self-test of the HDC core at 27 MHz. LED 0 lights if every score matched the
// Python model, LED 1 if one did not, LED 2 while it runs; LED 5 blinks to show the clock runs.
`default_nettype none

module top (
    input  wire       clk,  // 27 MHz, pin 4
    output wire [5:0] led   // pins 15 to 20, active low
);
  // Hold reset for 15 cycles after configuration.
  reg [3:0] por = 4'd0;
  always @(posedge clk) if (~&por) por <= por + 4'd1;

  reg [23:0] heartbeat = 24'd0;
  always @(posedge clk) heartbeat <= heartbeat + 24'd1;

  wire busy, passed, failed;
  hdc_selftest u_test (
      .clk(clk),
      .rst(~&por),
      .busy(busy),
      .passed(passed),
      .failed(failed)
  );

  assign led = ~{heartbeat[23], 2'b00, busy, failed, passed};
endmodule

`default_nettype wire
