// File: inc_comb.v
// Generated by MyHDL 0.7
// Date: Sun Dec 19 16:52:33 2010


`timescale 1ns/10ps

module inc_comb (
    nextCount,
    count
);


output [7:0] nextCount;
wire [7:0] nextCount;
input [7:0] count;







assign nextCount = (count + 1) % 256;

endmodule
