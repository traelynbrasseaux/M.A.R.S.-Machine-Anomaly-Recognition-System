// UPduino v3.1 bring-up: the internal 48 MHz oscillator, divided to 24 MHz, steps the RGB LED
// through red, green, blue and white, about 0.7 s each.
`default_nettype none

module top (
    output wire led_green,  // RGB0, pin 39
    output wire led_blue,   // RGB1, pin 40
    output wire led_red     // RGB2, pin 41
);
  wire clk;
  SB_HFOSC #(
      .CLKHF_DIV("0b01")
  ) osc (
      .CLKHFPU(1'b1),
      .CLKHFEN(1'b1),
      .CLKHF(clk)
  );

  reg [25:0] count = 26'd0;
  always @(posedge clk) count <= count + 26'd1;
  wire [1:0] phase = count[25:24];

  // The LED pins sink current through the RGB driver; minimum current is plenty.
  SB_RGBA_DRV #(
      .CURRENT_MODE("0b1"),
      .RGB0_CURRENT("0b000001"),
      .RGB1_CURRENT("0b000001"),
      .RGB2_CURRENT("0b000001")
  ) rgb (
      .CURREN(1'b1),
      .RGBLEDEN(1'b1),
      .RGB0PWM(phase == 2'd1 || phase == 2'd3),
      .RGB1PWM(phase == 2'd2 || phase == 2'd3),
      .RGB2PWM(phase == 2'd0 || phase == 2'd3),
      .RGB0(led_green),
      .RGB1(led_blue),
      .RGB2(led_red)
  );
endmodule

`default_nettype wire
