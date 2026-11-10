// Binary HDC detector: LFSR item memory, compare-only quantizer, XOR binding, majority
// bundling and Hamming scoring, bit-exact with host/mhm/fixed/hdc.py.
// One narrow sequential datapath (16 dimensions per step) trades speed for LUTs on the UP5K.
`default_nettype none

module hdc_core #(
    parameter DIM = 4096,  // hypervector bits: a power of two from 256 to 4096
    parameter NF_MAX = 45,  // feature IDs drawn from the LFSR, at most 63
    parameter [31:0] SEED = 32'h1234ABCD
) (
    input wire clk,
    input wire rst,
    output wire ready,  // item memory built and no operation running

    // One operation on context op_ctx (say 0 accel, 1 audio), accepted while ready.
    input wire       op_valid,
    input wire [1:0] op,        // OP_CLEAR, OP_BUNDLE, OP_FINISH, OP_SCORE
    input wire       op_ctx,
    input wire [5:0] op_nfeat,  // features per window for BUNDLE and SCORE, at least 1

    // Q16.16 features of one window for BUNDLE and SCORE, in feature order.
    input  wire        feat_valid,
    output wire        feat_ready,
    input  wire [31:0] feat_data,

    // Threshold write while ready (takes priority over op_valid): Q16.16 lower edge of level
    // thr_level (1 to 15) for one feature, as made by level_thresholds in the Python.
    input wire        thr_we,
    input wire        thr_ctx,
    input wire [ 5:0] thr_feat,
    input wire [ 3:0] thr_level,
    input wire [31:0] thr_value,

    output reg        done,  // one-cycle pulse when an operation finishes
    output reg [15:0] score  // after SCORE: Hamming distance to the prototype, 0 to DIM
);
  localparam [1:0] OP_CLEAR = 2'd0, OP_BUNDLE = 2'd1, OP_FINISH = 2'd2, OP_SCORE = 2'd3;
  localparam [31:0] LFSR_TAPS = 32'h80200003;  // x^32 + x^22 + x^2 + x + 1, as in hdc.py
  localparam AW = 14;
  localparam W = DIM / 16;  // 16-bit words per hypervector

  // Item memory: 16 level vectors (level 0 is the base), the tiebreak vector, then the IDs.
  localparam TIE_BASE = 16 * W;
  localparam ID_BASE = 17 * W;
  localparam IM_DEPTH = (17 + NF_MAX) * W;
  // Context memory: thresholds at {ctx, feature, level, half}, then votes, then prototypes.
  localparam VOTE_BASE = 4096;
  localparam PROTO_BASE = VOTE_BASE + 2 * DIM;
  localparam CM_DEPTH = PROTO_BASE + 2 * W;

  localparam [4:0]
      G_STREAM = 5'd0,  // LFSR bits into words: tiebreak, then base, then (after levels) IDs
      G_THR = 5'd1,  // shift in one dimension's 16-bit threshold for this level pass
      G_FLIP = 5'd2,  // compare it with the level's cut
      G_RDBASE = 5'd3,
      G_WRLVL = 5'd4,  // level word = base word ^ flips
      S_IDLE = 5'd5,
      S_THR_HI = 5'd6,
      S_CLEAR = 5'd7,
      Q_WAIT = 5'd8,  // quantizer: wait for a feature, then compare it with 15 thresholds
      Q_RDLO = 5'd9,
      Q_RDHI = 5'd10,
      Q_CMP = 5'd11,
      E_TIE = 5'd12,  // encoder, per 16-dimension chunk: tiebreak word, then per feature
      E_ID = 5'd13,  // read its ID word,
      E_LV = 5'd14,  // read its level word,
      E_ACC = 5'd15,  // count the bound bits
      E_END = 5'd16,
      E_SCORE = 5'd17,
      E_VRD = 5'd18,  // bundle: read-modify-write 16 vote counters
      E_VWR = 5'd19,
      F_TIE = 5'd20,  // finish: prototype bit = majority of the votes, per dimension
      F_RD = 5'd21,
      F_ACC = 5'd22,
      F_WR = 5'd23;

  // Addresses are worked out in 32-bit integer arithmetic, then truncated to AW bits.
  /* verilator lint_off UNUSEDSIGNAL */
  function [AW-1:0] a14;
    input integer v;
    a14 = v[AW-1:0];
  endfunction
  /* verilator lint_on UNUSEDSIGNAL */

  function [4:0] popcount16;
    input [15:0] v;
    integer j;
    begin
      popcount16 = 5'd0;
      for (j = 0; j < 16; j = j + 1) popcount16 = popcount16 + {4'd0, v[j]};
    end
  endfunction

  reg [4:0] state;
  reg [1:0] gphase;  // G_STREAM region: 0 tiebreak, 1 base, 2 IDs
  reg [31:0] lfsr, snapshot;  // snapshot: LFSR state at the first threshold bit
  reg [AW-1:0] gaddr;  // G_STREAM write address, or the vote index in S_CLEAR
  reg [AW-1:0] w;  // word index within a hypervector
  reg [3:0] b;  // bit within a word
  reg [3:0] k;  // threshold level, a feature's level in the encoder, or dimensions per word
  reg [3:0] lvl;  // level pass
  reg [5:0] f;  // feature
  reg [15:0] word;  // word being built, threshold high half, or ID word
  reg [15:0] aux;  // threshold being shifted in, threshold low half, or tiebreak word
  reg [31:0] x;  // feature being quantized
  reg [3:0] q;  // its level so far
  reg [1:0] op_r;
  reg ctx;
  reg [5:0] nfeat;
  reg [15:0] mism;
  reg [15:0] n_bundled0, n_bundled1;
  reg [6*16-1:0] ones;  // per chunk dimension: bound vectors voting 1
  integer i;

  reg im_we, cm_we;
  reg [AW-1:0] im_addr, cm_addr;
  reg [15:0] im_wdata, cm_wdata;
  wire [15:0] im_rdata, cm_rdata;

  spram #(
      .DEPTH(IM_DEPTH),
      .AW(AW)
  ) u_item (
      .clk(clk),
      .we(im_we),
      .addr(im_addr),
      .wdata(im_wdata),
      .rdata(im_rdata)
  );

  spram #(
      .DEPTH(CM_DEPTH),
      .AW(AW)
  ) u_ctx (
      .clk(clk),
      .we(cm_we),
      .addr(cm_addr),
      .wdata(cm_wdata),
      .rdata(cm_rdata)
  );

  // Quantized levels of the current window, one per feature.
  reg [3:0] qram[0:63];
  reg [3:0] qram_q;
  wire [5:0] qram_raddr = (state == E_ACC) ? f + 6'd1 : f;

  wire [31:0] lfsr_next = {1'b0, lfsr[31:1]} ^ (lfsr[0] ? LFSR_TAPS : 32'd0);
  wire [AW-1:0] stream_last = (gphase == 2'd0) ? a14(TIE_BASE + W - 1) :
                              (gphase == 2'd1) ? a14(W - 1) : a14(IM_DEPTH - 1);
  // thr < lvl * 2^15 / 15 without a divider; see level_flips in host/mhm/fixed/hdc.py.
  wire [20:0] thr_scaled = ({5'd0, aux} + 21'd1) * 21'd15;
  wire flip = thr_scaled <= {2'd0, lvl, 15'd0};

  wire reach = $signed({cm_rdata, aux}) <= $signed(x);
  wire [3:0] q_next = q + {3'd0, reach};

  wire [15:0] bound = word ^ im_rdata;  // E_ACC: ID word ^ level word
  wire [15:0] enc;  // the window's majority bits for this chunk
  reg [15:0] enc_r;  // registered in E_END, which keeps the UP5K above 24 MHz
  genvar gi;
  generate
    for (gi = 0; gi < 16; gi = gi + 1) begin : g_dim
      wire [6:0] twice = {ones[6*gi+:6], 1'b0};
      assign enc[gi] = (twice == {1'b0, nfeat}) ? aux[gi] : (twice > {1'b0, nfeat});
    end
  endgenerate

  wire [15:0] n_bundled = ctx ? n_bundled1 : n_bundled0;
  wire [16:0] votes_twice = {cm_rdata, 1'b0};
  wire proto_bit = (votes_twice == {1'b0, n_bundled}) ? aux[b] : (votes_twice > {1'b0, n_bundled});
  wire [4:0] chunk_mism = popcount16(enc_r ^ cm_rdata);

  wire last_word = w == a14(W - 1);
  /* verilator lint_off WIDTHEXPAND */
  wire [AW-1:0] gen_level_addr = a14(lvl * W + w);
  wire [AW-1:0] tie_addr = a14(TIE_BASE + w);
  wire [AW-1:0] id_addr = a14(ID_BASE + f * W + w);
  wire [AW-1:0] level_addr = a14(k * W + w);
  wire [AW-1:0] clear_addr = a14(VOTE_BASE + ctx * DIM + gaddr);
  wire [AW-1:0] vote_addr = a14(VOTE_BASE + ctx * DIM + w * 16 + b);
  wire [AW-1:0] proto_addr = a14(PROTO_BASE + ctx * W + w);
  /* verilator lint_on WIDTHEXPAND */

  assign ready = state == S_IDLE;
  assign feat_ready = state == Q_WAIT;

  always @(posedge clk) begin
    if (state == Q_CMP && k == 4'd15) qram[f] <= q_next;
    qram_q <= qram[qram_raddr];
  end

  // Memory ports, driven from the registered state only.
  always @* begin
    im_we = 1'b0;
    im_addr = {AW{1'b0}};
    im_wdata = 16'd0;
    cm_we = 1'b0;
    cm_addr = {AW{1'b0}};
    cm_wdata = 16'd0;
    case (state)
      G_STREAM: begin
        im_we = b == 4'd15;
        im_addr = gaddr;
        im_wdata = {lfsr[0], word[15:1]};
      end
      G_RDBASE: im_addr = w;
      G_WRLVL: begin
        im_we = 1'b1;
        im_addr = gen_level_addr;
        im_wdata = im_rdata ^ word;
      end
      S_IDLE:
      if (thr_we) begin
        cm_we = 1'b1;
        cm_addr = {2'b00, thr_ctx, thr_feat, thr_level, 1'b0};
        cm_wdata = thr_value[15:0];
      end
      S_THR_HI: begin
        cm_we = 1'b1;
        cm_addr = {2'b00, ctx, f, k, 1'b1};
        cm_wdata = word;
      end
      S_CLEAR: begin
        cm_we = 1'b1;
        cm_addr = clear_addr;
      end
      Q_RDLO: cm_addr = {2'b00, ctx, f, k, 1'b0};
      Q_RDHI: cm_addr = {2'b00, ctx, f, k, 1'b1};
      E_TIE: im_addr = tie_addr;
      E_ID: im_addr = id_addr;
      E_LV: im_addr = level_addr;
      E_END: cm_addr = proto_addr;
      E_VRD: cm_addr = vote_addr;
      E_VWR: begin
        cm_we = 1'b1;
        cm_addr = vote_addr;
        cm_wdata = cm_rdata + {15'd0, enc_r[b]};
      end
      F_TIE: im_addr = tie_addr;
      F_RD: cm_addr = vote_addr;
      F_WR: begin
        cm_we = 1'b1;
        cm_addr = proto_addr;
        cm_wdata = word;
      end
      default: ;
    endcase
  end

  always @(posedge clk) begin
    done <= 1'b0;
    if (rst) begin
      state <= G_STREAM;
      gphase <= 2'd0;
      lfsr <= SEED;
      gaddr <= a14(TIE_BASE);
      b <= 4'd0;
      n_bundled0 <= 16'd0;
      n_bundled1 <= 16'd0;
      score <= 16'd0;
    end else begin
      case (state)
        G_STREAM: begin
          lfsr <= lfsr_next;
          word <= {lfsr[0], word[15:1]};
          b <= b + 4'd1;
          if (b == 4'd15) begin
            gaddr <= gaddr + 1'b1;
            if (gaddr == stream_last) begin
              case (gphase)
                2'd0: begin
                  gphase <= 2'd1;
                  gaddr  <= {AW{1'b0}};
                end
                2'd1: begin
                  snapshot <= lfsr_next;
                  lvl <= 4'd1;
                  w <= {AW{1'b0}};
                  k <= 4'd0;
                  state <= G_THR;
                end
                default: state <= S_IDLE;
              endcase
            end
          end
        end
        G_THR: begin
          lfsr <= lfsr_next;
          aux <= {lfsr[0], aux[15:1]};
          b <= b + 4'd1;
          if (b == 4'd15) state <= G_FLIP;
        end
        G_FLIP: begin
          word <= {flip, word[15:1]};
          k <= k + 4'd1;
          state <= (k == 4'd15) ? G_RDBASE : G_THR;
        end
        G_RDBASE: state <= G_WRLVL;
        G_WRLVL: begin
          if (last_word) begin
            w <= {AW{1'b0}};
            if (lvl == 4'd15) begin
              // The last pass leaves the LFSR at the first ID bit.
              gphase <= 2'd2;
              gaddr <= a14(ID_BASE);
              state <= G_STREAM;
            end else begin
              lvl <= lvl + 4'd1;
              lfsr <= snapshot;
              state <= G_THR;
            end
          end else begin
            w <= w + 1'b1;
            state <= G_THR;
          end
        end

        S_IDLE:
        if (thr_we) begin
          word <= thr_value[31:16];
          ctx <= thr_ctx;
          f <= thr_feat;
          k <= thr_level;
          state <= S_THR_HI;
        end else if (op_valid) begin
          op_r <= op;
          ctx <= op_ctx;
          nfeat <= op_nfeat;
          f <= 6'd0;
          w <= {AW{1'b0}};
          b <= 4'd0;
          gaddr <= {AW{1'b0}};
          mism <= 16'd0;
          case (op)
            OP_CLEAR: state <= S_CLEAR;
            OP_FINISH: state <= F_TIE;
            OP_BUNDLE, OP_SCORE: state <= Q_WAIT;
            default: ;
          endcase
        end
        S_THR_HI: state <= S_IDLE;
        S_CLEAR: begin
          gaddr <= gaddr + 1'b1;
          if (gaddr == a14(DIM - 1)) begin
            if (ctx) n_bundled1 <= 16'd0;
            else n_bundled0 <= 16'd0;
            done  <= 1'b1;
            state <= S_IDLE;
          end
        end

        Q_WAIT:
        if (feat_valid) begin
          x <= feat_data;
          q <= 4'd0;
          k <= 4'd1;
          state <= Q_RDLO;
        end
        Q_RDLO: state <= Q_RDHI;
        Q_RDHI: begin
          aux   <= cm_rdata;
          state <= Q_CMP;
        end
        Q_CMP: begin
          q <= q_next;
          if (k == 4'd15) begin
            if (f + 6'd1 == nfeat) begin
              f <= 6'd0;
              state <= E_TIE;
            end else begin
              f <= f + 6'd1;
              state <= Q_WAIT;
            end
          end else begin
            k <= k + 4'd1;
            state <= Q_RDLO;
          end
        end

        E_TIE: begin
          ones  <= {6 * 16{1'b0}};
          state <= E_ID;
        end
        E_ID: begin
          if (f == 6'd0) aux <= im_rdata;
          k <= qram_q;
          state <= E_LV;
        end
        E_LV: begin
          word  <= im_rdata;
          state <= E_ACC;
        end
        E_ACC: begin
          for (i = 0; i < 16; i = i + 1) ones[6*i+:6] <= ones[6*i+:6] + {5'd0, bound[i]};
          f <= f + 6'd1;
          state <= (f + 6'd1 == nfeat) ? E_END : E_ID;
        end
        E_END: begin
          enc_r <= enc;
          state <= (op_r == OP_SCORE) ? E_SCORE : E_VRD;
        end
        E_SCORE: begin
          mism <= mism + {11'd0, chunk_mism};
          if (last_word) begin
            score <= mism + {11'd0, chunk_mism};
            done  <= 1'b1;
            state <= S_IDLE;
          end else begin
            w <= w + 1'b1;
            f <= 6'd0;
            state <= E_TIE;
          end
        end
        E_VRD: state <= E_VWR;
        E_VWR: begin
          b <= b + 4'd1;
          if (b != 4'd15) state <= E_VRD;
          else if (last_word) begin
            if (ctx) n_bundled1 <= n_bundled1 + 16'd1;
            else n_bundled0 <= n_bundled0 + 16'd1;
            done  <= 1'b1;
            state <= S_IDLE;
          end else begin
            w <= w + 1'b1;
            f <= 6'd0;
            state <= E_TIE;
          end
        end

        F_TIE: begin
          b <= 4'd0;
          state <= F_RD;
        end
        F_RD: begin
          if (b == 4'd0) aux <= im_rdata;
          state <= F_ACC;
        end
        F_ACC: begin
          word <= {proto_bit, word[15:1]};
          b <= b + 4'd1;
          state <= (b == 4'd15) ? F_WR : F_RD;
        end
        F_WR:
        if (last_word) begin
          done  <= 1'b1;
          state <= S_IDLE;
        end else begin
          w <= w + 1'b1;
          state <= F_TIE;
        end

        default: state <= S_IDLE;
      endcase
    end
  end
endmodule

`default_nettype wire
