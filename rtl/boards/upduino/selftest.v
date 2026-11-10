// UPduino v3.1 self-test of the HDC core at 24 MHz: the RGB LED shows blue while it runs,
// then green if every score matched the Python model or red if one did not.
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

  // Hold reset for 15 cycles after configuration.
  reg [3:0] por = 4'd0;
  always @(posedge clk) if (~&por) por <= por + 4'd1;

  wire busy, passed, failed;
  hdc_selftest u_test (
      .clk(clk),
      .rst(~&por),
      .busy(busy),
      .passed(passed),
      .failed(failed)
  );

  SB_RGBA_DRV #(
      .CURRENT_MODE("0b1"),
      .RGB0_CURRENT("0b000001"),
      .RGB1_CURRENT("0b000001"),
      .RGB2_CURRENT("0b000001")
  ) rgb (
      .CURREN(1'b1),
      .RGBLEDEN(1'b1),
      .RGB0PWM(passed),
      .RGB1PWM(busy),
      .RGB2PWM(failed),
      .RGB0(led_green),
      .RGB1(led_blue),
      .RGB2(led_red)
  );
endmodule

`default_nettype wire
