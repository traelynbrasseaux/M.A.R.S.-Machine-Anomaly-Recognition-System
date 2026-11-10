// Tang Nano 20K bring-up: the 27 MHz crystal drives a binary count on the six LEDs,
// the lowest one toggling about every 0.3 s.
`default_nettype none

module top (
    input  wire       clk,  // 27 MHz, pin 4
    output wire [5:0] led   // pins 15 to 20, active low
);
  reg [28:0] count = 29'd0;
  always @(posedge clk) count <= count + 29'd1;
  assign led = ~count[28:23];
endmodule

`default_nettype wire
