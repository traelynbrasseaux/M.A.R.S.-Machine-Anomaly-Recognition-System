// On-chip check of hdc_core: replays a ROM made by rtl/scripts/selftest_rom.py (level edges,
// training windows, test windows each followed by FixedHdc's score) and flags any mismatch.
`default_nettype none

module hdc_selftest #(
    parameter DIM = 4096,
    parameter NF_MAX = 45,
    parameter ROM_FILE = "build/selftest.hex",  // relative to where the tool runs
    parameter ROM_AW = 10
) (
    input  wire clk,
    input  wire rst,
    output wire busy,
    output reg  passed,
    output reg  failed
);
  localparam [1:0] OP_CLEAR = 2'd0, OP_BUNDLE = 2'd1, OP_FINISH = 2'd2, OP_SCORE = 2'd3;
  localparam [2:0]
      S_FETCH = 3'd0,  // wait one cycle for the ROM word at the new address
      S_HEADER = 3'd1,  // feature count, training windows, test windows
      S_THR = 3'd2,
      S_OP = 3'd3,
      S_FEED = 3'd4,
      S_WAIT = 3'd5,
      S_CHECK = 3'd6,
      S_END = 3'd7;

  reg [31:0] rom[0:(1<<ROM_AW)-1];
  initial $readmemh(ROM_FILE, rom);
  reg [ROM_AW-1:0] ra;
  reg [31:0] rq;
  always @(posedge clk) rq <= rom[ra];

  reg [2:0] state, ret;
  reg [1:0] phase;  // the operation S_OP issues next
  reg [1:0] hdr;
  reg [5:0] nfeat, feat;
  reg [3:0] level;
  reg [7:0] n_train, n_test, win;
  reg mismatch;

  wire core_ready, feat_ready, done;
  wire [15:0] score;

  hdc_core #(
      .DIM(DIM),
      .NF_MAX(NF_MAX)
  ) u_core (
      .clk(clk),
      .rst(rst),
      .ready(core_ready),
      .op_valid(state == S_OP && core_ready),
      .op(phase),
      .op_ctx(1'b0),
      .op_nfeat(nfeat),
      .feat_valid(state == S_FEED),
      .feat_ready(feat_ready),
      .feat_data(rq),
      .thr_we(state == S_THR && core_ready),
      .thr_ctx(1'b0),
      .thr_feat(feat),
      .thr_level(level),
      .thr_value(rq),
      .done(done),
      .score(score)
  );

  assign busy = state != S_END;

  always @(posedge clk) begin
    if (rst) begin
      state <= S_FETCH;
      ret <= S_HEADER;
      ra <= {ROM_AW{1'b0}};
      hdr <= 2'd0;
      mismatch <= 1'b0;
      passed <= 1'b0;
      failed <= 1'b0;
    end else begin
      case (state)
        S_FETCH: state <= ret;
        S_HEADER: begin
          case (hdr)
            2'd0: nfeat <= rq[5:0];
            2'd1: n_train <= rq[7:0];
            default: n_test <= rq[7:0];
          endcase
          hdr <= hdr + 2'd1;
          ra <= ra + 1'b1;
          feat <= 6'd0;
          level <= 4'd1;
          state <= S_FETCH;
          ret <= (hdr == 2'd2) ? S_THR : S_HEADER;
        end
        S_THR:
        if (core_ready) begin
          ra <= ra + 1'b1;
          state <= S_FETCH;
          if (level == 4'd15) begin
            level <= 4'd1;
            feat <= feat + 6'd1;
            phase <= OP_CLEAR;
            ret <= (feat == nfeat - 6'd1) ? S_OP : S_THR;
          end else begin
            level <= level + 4'd1;
            ret <= S_THR;
          end
        end
        S_OP:
        if (core_ready) begin
          feat  <= 6'd0;
          state <= (phase == OP_BUNDLE || phase == OP_SCORE) ? S_FEED : S_WAIT;
        end
        S_FEED:
        if (feat_ready) begin
          ra <= ra + 1'b1;
          feat <= feat + 6'd1;
          state <= S_FETCH;
          ret <= (feat == nfeat - 6'd1) ? S_WAIT : S_FEED;
        end
        S_WAIT:
        if (done) begin
          win <= win + 8'd1;
          state <= S_OP;
          case (phase)
            OP_CLEAR: begin
              phase <= OP_BUNDLE;
              win   <= 8'd0;
            end
            OP_BUNDLE: if (win == n_train - 8'd1) phase <= OP_FINISH;
            OP_FINISH: begin
              phase <= OP_SCORE;
              win   <= 8'd0;
            end
            default: begin
              win   <= win;
              state <= S_CHECK;  // rq holds this window's expected score
            end
          endcase
        end
        S_CHECK: begin
          if (score != rq[15:0]) mismatch <= 1'b1;
          ra <= ra + 1'b1;
          win <= win + 8'd1;
          state <= S_FETCH;
          ret <= (win == n_test - 8'd1) ? S_END : S_OP;
        end
        default: begin
          passed <= !mismatch;
          failed <= mismatch;
        end
      endcase
    end
  end
endmodule

`default_nettype wire
