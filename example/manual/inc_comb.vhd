-- File: inc_comb.vhd
-- Generated by MyHDL 0.7dev
-- Date: Fri Jul  2 13:23:51 2010

library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use std.textio.all;

use work.pck_myhdl_07dev.all;

entity inc_comb is
    port (
        nextCount: out unsigned(7 downto 0);
        count: in unsigned(7 downto 0)
    );
end entity inc_comb;


architecture MyHDL of inc_comb is


begin





nextCount <= (count + 1) mod 256;

end architecture MyHDL;
