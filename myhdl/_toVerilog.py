#  This file is part of the myhdl library, a Python package for using
#  Python as a Hardware Description Language.
#
#  Copyright (C) 2003 Jan Decaluwe
#
#  The myhdl library is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public License as
#  published by the Free Software Foundation; either version 2.1 of the
#  License, or (at your option) any later version.
#
#  This library is distributed in the hope that it will be useful, but
#  WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this library; if not, write to the Free Software
#  Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA

""" myhdl toVerilog module.

"""

__author__ = "Jan Decaluwe <jan@jandecaluwe.com>"
__revision__ = "$Revision$"
__date__ = "$Date$"

import inspect
import operator
import compiler
from compiler import ast
from sets import Set
from types import GeneratorType, ClassType
from cStringIO import StringIO

import myhdl
from myhdl import *
from myhdl import ToVerilogError
from myhdl._extractHierarchy import _HierExtr, _findInstanceName
from myhdl._util import _flatten
from myhdl._unparse import _unparse
            

_converting = 0
_profileFunc = None

class _error:
    pass
_error.ArgType = "toVerilog first argument should be a classic function"
_error.NotSupported = "Not supported"
_error.TopLevelName = "Result of toVerilog call should be assigned to a top level name"
_error.SigMultipleDriven = "Signal has multiple drivers"
_error.UndefinedBitWidth = "Signal has undefined bit width"
_error.UndrivenSignal = "Signal is not driven"
_error.Requirement = "Requirement violation"
    

def _checkArgs(arglist):
    for arg in arglist:
        if not type(arg) is GeneratorType:
            raise ArgumentError
        

def toVerilog(func, *args, **kwargs):
    global _converting
    if _converting:
        return func(*args, **kwargs) # skip
    if not callable(func):
        raise ToVerilogError(_error.ArgType, "got %s" % type(func))
    _converting = 1
    try:
        outer = inspect.getouterframes(inspect.currentframe())[1]
        name = _findInstanceName(outer)
        if name is None:
            raise TopLevelNameError
        h = _HierExtr(name, func, *args, **kwargs)
    finally:
        _converting = 0
    vpath = name + ".v"
    vfile = open(vpath, 'w')
    tbpath = "tb_" + vpath
    tbfile = open(tbpath, 'w')
    
    siglist = _analyzeSigs(h.hierarchy)
    arglist = _flatten(h.top)
    _checkArgs(arglist)
    genlist = _analyzeGens(arglist, h.gennames)
    intf = _analyzeTopFunc(func, *args, **kwargs)
    intf.name = name
    
    _writeModuleHeader(vfile, intf)
    _writeSigDecls(vfile, intf, siglist)
    _convertGens(genlist, vfile)
    _writeModuleFooter(vfile)
    _writeTestBench(tbfile, intf)

    vfile.close()
    tbfile.close()
    
    return h.top


def _analyzeSigs(hierarchy):
    curlevel = 0
    siglist = []
    prefixes = []
    for level, name, sigdict in hierarchy:
        delta = curlevel - level
        curlevel = level
        assert(delta >= -1)
        if delta == -1:
            prefixes.append(name)
        elif delta == 0:
            prefixes.pop()
            prefixes.append(name)
        else:
            prefixes = prefixes[:curlevel]
        for n, s in sigdict.items():
            if s._name is None:
                if len(prefixes) > 1:
                    s._name = '_'.join(prefixes[1:]) + '_' + n
                else:
                    s._name = n
                siglist.append(s)
            if not s._nrbits:
                raise ToVerilogError(_error.UndefinedBitWidth, s._name)
    return siglist


def LabelGenerator():
    i = 1
    while 1:
        yield "_MYHDL_LABEL_%s" % i
        i += 1
        

def _analyzeGens(top, gennames):
    genLabel = LabelGenerator()
    genlist = []
    for g in top:
        f = g.gi_frame
        s = inspect.getsource(f)
        s = s.lstrip()
        gen = compiler.parse(s)
        gen.sourcefile = inspect.getsourcefile(f)
        gen.lineoffset = inspect.getsourcelines(f)[1]-1
        symdict = f.f_globals.copy()
        symdict.update(f.f_locals)
        # print f.f_locals
        sigdict = {}
        for n, v in symdict.items():
            if isinstance(v, Signal):
                sigdict[n] = v
        gen.sigdict = sigdict
        gen.symdict = symdict
        if gennames.has_key(id(g)):
            gen.name = gennames[id(g)]
        else:
            gen.name = genLabel.next()
        v = _AnalyzeGenVisitor(sigdict, symdict, gen.sourcefile, gen.lineoffset)
        compiler.walk(gen, v)
        gen.vardict = v.vardict
        gen.kind = v.kind
        genlist.append(gen)
    return genlist


class _ToVerilogMixin(object):
    
    def getLineNo(self, node):
        lineno = node.lineno
        if lineno is None:
            for n in node.getChildNodes():
                if n.lineno is not None:
                    lineno = n.lineno
                    break
        lineno = lineno or 0
        return lineno
    
    def getVal(self, node):
        val = eval(_unparse(node), self.symdict)
        return val
    
    def raiseError(self, node, kind, msg=""):
        lineno = self.getLineNo(node)
        info = "in file %s, line %s:\n    " % \
              (self.sourcefile, self.lineoffset+lineno)
        raise ToVerilogError(kind, msg, info)

    def require(self, node, test, msg=""):
        assert isinstance(node, ast.Node)
        if not test:
            self.raiseError(node, _error.Requirement, msg)
   

class _NotSupportedVisitor(_ToVerilogMixin):

    
    def visitAssList(self, node, *args):
        self.raiseError(node, _error.NotSupported, "list assignment")
        
    def visitAssTuple(self, node, *args):
        self.raiseError(node, _error.NotSupported, "tuple assignment")
        
    def visitBackquote(self, node, *args):
        self.raiseError(node, _error.NotSupported, "backquote")
        
    def visitBreak(self, node, *args):
        self.raiseError(node, _error.NotSupported, "break statement")
        
    def visitClass(self, node, *args):
        self.raiseError(node, _error.NotSupported, "class statement")

    def visitContinue(self, node, *args):
        self.raiseError(node, _error.NotSupported, "continue statement")

    def visitDict(self, node, *args):
        self.raiseError(node, _error.NotSupported, "dictionary")

    def visitDiv(self, node, *args):
        self.raiseError(node, _error.NotSupported, "true division - consider '//'")

    def visitEllipsis(self, node, *args):
        self.raiseError(node, _error.NotSupported, "ellipsis")

    def visitExec(self, node, *args):
        self.raiseError(node, _error.NotSupported, "exec statement")

    def visitExpression(self, node, *args):
        self.raiseError(node, _error.NotSupported, "Expression node")

    def visitFrom(self, node, *args):
        self.raiseError(node, _error.NotSupported, "from statement")
        
    def visitGlobal(self, node, *args):
        self.raiseError(node, _error.NotSupported, "global statement")

    def visitImport(self, node, *args):
        self.raiseError(node, _error.NotSupported, "import statement")

    def visitLambda(self, node, *args):
        self.raiseError(node, _error.NotSupported, "lambda statement")

    def visitListComp(self, node, *args):
        self.raiseError(node, _error.NotSupported, "list comprehension")
        
    def visitList(self, node, *args):
        self.raiseError(node, _error.NotSupported, "list")

    def visitReturn(self, node, *args):
        self.raiseError(node, _error.NotSupported, "return statement")

    def visitTryExcept(self, node, *args):
        self.raiseError(node, _error.NotSupported, "try-except statement")
        
    def visitTryFinally(self, node, *args):
        self.raiseError(node, _error.NotSupported, "try-finally statement")


def getObj(node):
    if hasattr(node, 'obj'):
        return node.obj
    return None

  
INPUT, OUTPUT, INOUT = range(3)
NORMAL, DECLARATION = range(2)
ALWAYS, INITIAL = range(2)

class _AnalyzeGenVisitor(_NotSupportedVisitor, _ToVerilogMixin):
    
    def __init__(self, sigdict, symdict, sourcefile, lineoffset):
        self.sourcefile = sourcefile
        self.lineoffset = lineoffset
        self.inputs = Set()
        self.outputs = Set()
        self.toplevel = 1
        self.sigdict = sigdict
        self.symdict = symdict
        self.vardict = {}

    def getObj(self, node):
        if hasattr(node, 'obj'):
            return node.obj
        return None

    def getKind(self, node):
        if hasattr(node, 'kind'):
            return node.kind
        return None

    def getVal(self, node):
        val = eval(_unparse(node), self.symdict)
        return val
        
    def visitAssAttr(self, node, access=OUTPUT, *args):
        self.visit(node.expr, OUTPUT)
        
    def visitAssign(self, node, access=OUTPUT, *args):
        if len(node.nodes) > 1:
            self.raiseError(node, _error.NotSupported, "multiple assignments")
        target, expr = node.nodes[0], node.expr
        self.visit(target, OUTPUT)
        if isinstance(target, ast.AssName):
            self.visit(expr, INPUT, DECLARATION)
            node.kind = DECLARATION
            n = target.name
            obj = self.getObj(expr)
            if obj is None:
                self.raiseError(node, "Cannot infer type or bit width of %s" % n)
            self.vardict[n] = obj
            # XXX if n is already in vardict
        else:
            self.visit(expr, INPUT)

    def visitAssName(self, node, *args):
        n = node.name
        self.require(node, n not in self.symdict, "Illegal redeclaration: %s" % n)
        
    def visitAugAssign(self, node, access=INPUT, *args):
        self.visit(node.node, INOUT)
        self.visit(node.expr, INPUT)

    def visitCallFunc(self, node, *args):
        for child in node.getChildNodes():
            self.visit(child, *args)
        f = self.getObj(node.node)
        # print f
        if type(f) is type and issubclass(f, intbv):
            node.obj = intbv()
            
    def visitCompare(self, node, *args):
        node.obj = bool()

    def visitConst(self, node, *args):
        if isinstance(node.value, bool):
            node.obj = bool()
        elif isinstance(node.value, int):
            node.obj = int()
        else:
            node.obj = None
            
    def visitFor(self, node, *args):
        assert isinstance(node.assign, ast.AssName)
        self.visit(node.assign)
        var = node.assign.name
        self.vardict[var] = int()
        self.visit(node.list)
        self.visit(node.body, *args)
        self.require(node, node.else_ is None, "for-else not supported")
        
    def visitFunction(self, node, *args):
        if not self.toplevel:
            self.raiseError(node, _error.NotSupported, "embedded function definition")
        self.toplevel = 0
        print node.code
        self.visit(node.code)
        self.kind = ALWAYS
        for n in node.code.nodes[:-1]:
            if not self.getKind(n) == DECLARATION:
                self.kind = INITIAL
                break
        if self.kind == ALWAYS:
            w = node.code.nodes[-1]
            if not self.getKind(w) == ALWAYS:
                self.kind = INITIAL

           
    def visitGetattr(self, node, *args):
        self.visit(node.expr, *args)
        assert isinstance(node.expr, ast.Name)
        assert node.expr.name in self.symdict
        obj = self.symdict[node.expr.name]
        if str(type(obj)) == "<class 'myhdl._enum.Enum'>":
            assert hasattr(obj, node.attrname)
            node.obj = getattr(obj, node.attrname)

    def visitModule(self, node, *args):
        print node
        #assert len(node.node.nodes) == 1
        #assert isinstance(node.node.nodes[0], ast.Function)
        self.visit(node.node)
        for n in self.outputs:
            s = self.sigdict[n]
            if s._driven:
                self.raiseError(node, _error._SigMultipleDriven, n)
            s._driven = True
        for n in self.inputs:
            s = self.sigdict[n]
            s._read = True

    def visitName(self, node, access=INPUT, *args):
        n = node.name
        if n in self.sigdict:
            if access == INPUT:
                self.inputs.add(n)
            elif access == OUTPUT:
                self.outputs.add(n)
            else: 
                raise AssertionError
        node.obj = None
        if n in self.vardict:
            node.obj = self.vardict[n]
        elif n in self.symdict:
            node.obj = self.symdict[n]
        elif n in __builtins__:
            node.obj = __builtins__[n]
            
    def visitSlice(self, node, access=INPUT, kind=NORMAL, *args):
        self.visit(node.expr, access)
        node.obj = self.getObj(node.expr)
        if node.lower:
            self.visit(node.lower, INPUT)
        if node.upper:
            self.visit(node.upper, INPUT)
        if isinstance(node.obj , intbv):
            if kind == DECLARATION:
                self.require(node.lower, "Expected leftmost index")
                leftind = self.getVal(node.lower)
                if node.upper:
                    rightind = self.getVal(node.upper)
                else:
                    rightind = 0
                node.obj = intbv()[leftind:rightind]
            
 
    def visitSubscript(self, node, access=INPUT, *args):
        self.visit(node.expr, access)
        for n in node.subs:
            self.visit(n, INPUT)
        node.obj = bool() # XXX 

    def visitWhile(self, node, *args):
        self.visit(node.test, *args)
        self.visit(node.body, *args)
        if isinstance(node.test, ast.Const) and \
           node.test.value == True and \
           isinstance(node.body.nodes[0], ast.Yield):
            node.kind = ALWAYS
        self.require(node, node.else_ is None, "while-else not supported")

       
                
def _analyzeTopFunc(func, *args, **kwargs):
    s = inspect.getsource(func)
    s = s.lstrip()
    funcast = compiler.parse(s)
    v = _AnalyzeTopFuncVisitor(*args, **kwargs)
    compiler.walk(funcast, v)
    return v
      
    
#Masks for co_flags
#define CO_OPTIMIZED	0x0001
#define CO_NEWLOCALS	0x0002
#define CO_VARARGS	0x0004
#define CO_VARKEYWORDS	0x0008
#define CO_NESTED       0x0010
#define CO_GENERATOR    0x0020
    
class _AnalyzeTopFuncVisitor(object):

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.name = None
        self.argdict = {}
    
    def visitFunction(self, node):
        if node.flags != 0: # check flags
            raise AssertionError("unsupported function type")
        self.name = node.name
        argnames = node.argnames
        i=-1
        for i, arg in enumerate(self.args):
            if isinstance(arg, Signal):
                n = argnames[i]
                self.argdict[n] = arg
        for n in argnames[i+1:]:
            if n in self.kwargs:
                arg = self.kwargs[n]
                if isinstance(arg, Signal):
                    self.argdict[n] = arg
        self.argnames = [n for n in argnames if n in self.argdict]


### Verilog output functions ###

def _writeModuleHeader(f, intf):
    print >> f, "module %s (" % intf.name
    b = StringIO()
    for portname in intf.argnames:
        print >> b, "    %s," % portname
    print >> f, b.getvalue()[:-2]
    b.close()
    print >> f, ");"
    print >> f
    for portname in intf.argnames:
        s = intf.argdict[portname]
        assert (s._name == portname)
        r = _getRangeString(s)
        if s._driven:
            print >> f, "output %s%s;" % (r, portname)
            print >> f, "reg %s%s;" % (r, portname)
        elif s._read:
            print >> f, "input %s%s;" % (r, portname)
    print >> f


def _writeSigDecls(f, intf, siglist):
    for s in siglist:
        if s._name in intf.argnames:
            continue
        r = _getRangeString(s)
        if s._driven:
            print >> f, "reg %s%s;" % (r, s._name)
        elif s._read:
            raise ToVerilogError(_error.UndrivenSignal, s._name)
    print >> f
            

def _writeModuleFooter(f):
    print >> f, "endmodule"
    

def _writeTestBench(f, intf):
    print >> f, "module tb_%s;" % intf.name
    print >> f
    fr = StringIO()
    to = StringIO()
    pm = StringIO()
    for portname in intf.argnames:
        s = intf.argdict[portname]
        r = _getRangeString(s)
        if s._driven:
            print >> f, "wire %s%s;" % (r, portname)
            print >> to, "        %s," % portname
        else:
            print >> f, "reg %s%s;" % (r, portname)
            print >> fr, "        %s," % portname
        print >> pm, "    %s," % portname
    print >> f
    print >> f, "initial begin"
    if fr.getvalue():
        print >> f, "    $from_myhdl("
        print >> f, fr.getvalue()[:-2]
        print >> f, "    );"
    if to.getvalue():
        print >> f, "    $to_myhdl("
        print >> f, to.getvalue()[:-2]
        print >> f, "    );" 
    print >> f, "end"
    print >> f
    print >> f, "%s dut(" % intf.name
    print >> f, pm.getvalue()[:-2]
    print >> f, ");"
    print >> f
    print >> f, "endmodule"


def _getRangeString(s):
    if s._type is bool:
        return ''
    elif s._type is intbv:
        return "[%s:0] " % (s._nrbits-1)
    elif s._nrbits is not None:
        return "[%s:0] " % (s._nrbits-1)
    else:
        raise AssertionError


def _convertGens(genlist, vfile):
    for gen in genlist:
        if gen.kind == ALWAYS:
            Visitor = _ConvertAlwaysVisitor
        else:
            Visitor = _ConvertInitialVisitor
        v = Visitor(vfile, gen.sigdict, gen.symdict, gen.vardict,
                    gen.name, gen.sourcefile, gen.lineoffset )
        compiler.walk(gen, v)


class _ConvertGenVisitor(_ToVerilogMixin):
    
    def __init__(self, f, sigdict, symdict, vardict, name, sourcefile, lineoffset):
        self.buf = self.fileBuf = f
        self.name = name
        self.sourcefile = sourcefile
        self.lineoffset = lineoffset
        self.declBuf = StringIO()
        self.codeBuf = StringIO()
        self.sigdict = sigdict
        self.symdict = symdict
        self.vardict = vardict
        print vardict
        self.ind = ''
        self.inYield = False
        self.isSigAss = False
 
    def write(self, arg):
        self.buf.write("%s" % arg)

    def writeline(self):
        self.buf.write("\n%s" % self.ind)

    def writeDeclarations(self):
        for name, obj in self.vardict.items():
            self.writeline()
            if type(obj) is bool:
                self.write("reg %s;" % name)
            elif isinstance(obj, int):
                self.write("integer %s;" % name)
            elif isinstance(obj, intbv):
                self.write("reg [%s-1:0] %s;" % (obj._len, name))
            elif hasattr(obj, '_nrbits'):
                self.write("reg [%s-1:0] %s;" % (obj._nrbits, name))
            else:
                raise AssertionError("unexpected type")
                
    def indent(self):
        self.ind += ' ' * 4

    def dedent(self):
        self.ind = self.ind[:-4]

    def binaryOp(self, node, op):
        self.write("(")
        self.visit(node.left)
        self.write(" %s " % op)
        self.visit(node.right)
        self.write(")")

    def multiOp(self, node, op):
        self.write("(")
        self.visit(node.nodes[0])
        for node in node.nodes[1:]:
            self.write(" %s " % op)
            self.visit(node)
        self.write(")")
        
    def visitAdd(self, node):
        self.binaryOp(node, '+')
        
    def visitAnd(self, node):
        self.multiOp(node, '&&')

    def visitAssAttr(self, node):
        # if not node.a
        # assert node.attrname == 'next'
        if node.attrname != 'next':
            self.raiseError(node, _error.NotSupported, "attribute assignment")
        self.isSigAss = True
        self.visit(node.expr)

    def visitAssert(self, node):
        # XXX
        pass

    def visitAssign(self, node):
        self.writeline()
        assert len(node.nodes) == 1
        self.visit(node.nodes[0])
        if self.isSigAss:
            self.write(' <= ')
            self.isSigAss = False
        else:
            self.write(' = ')
        self.visit(node.expr)
        self.write(';')

    def visitAssName(self, node):
        self.write(node.name)

    def visitAugAssign(self, node):
        # XXX
        pass

    def visitBitand(self, node):
        self.multiOp(node, '&')
        
    def visitBitor(self, node):
        self.multiOp(node, '|')
         
    def visitBitxor(self, node):
        self.multiOp(node, '^')

    def visitCallFunc(self, node):
        fn = node.node
        assert isinstance(fn, ast.Name)
        f = getObj(fn)
        opening, closing = '(', ')'
        if f is bool:
            self.write("(")
            self.visit(node.args[0])
            self.write(" ? 1'b1 : 1'b0)")
            return
        elif f is len:
            val = self.getVal(node)
            self.require(node, val is not None, "cannot calculate len")
            self.write(`val`)
            return
        elif type(f)  in (ClassType, type) and issubclass(f, Exception):
            self.write(f.__name__)
            self.write(": ")
        elif f is concat:
            opening, closing = '{', '}'
        else:
            self.write(f.__name__)
        self.write(opening)
        if node.args:
            self.visit(node.args[0])
            for arg in node.args[1:]:
                self.write(", ")
                self.visit(arg)
        self.write(closing)

    def visitCompare(self, node):
        self.write("(")
        self.visit(node.expr)
        assert len(node.ops) == 1
        op, code = node.ops[0]
        self.write(" %s " % op)
        self.visit(code)
        self.write(")")

    def visitConst(self, node):
        self.write(node.value)

    def visitFloorDiv(self, node):
        self.binaryOp(node, '/')

    def visitFor(self, node):
        var = node.assign.name
        self.buf = self.codeBuf
        cf = node.list
        self.require(node, isinstance(cf, ast.CallFunc), "Expected (down)range call")
        f = getObj(cf.node)
        self.require(node, f in (range, downrange), "Expected (down)range call")
        args = cf.args
        assert len(args) <= 3
        if f is range:
            cmp = '<'
            op = '+'
            oneoff = ''
            if len(args) == 1:
                start, stop, step = None, args[0], None
            elif len(args) == 2:
                start, stop, step = args[0], args[1], None
            else:
                start, stop, step = args
        else: # downrange
            cmp = '>='
            op = '-'
            oneoff ='-1'
            if len(args) == 1:
                start, stop, step = args[0], None, None
            elif len(args) == 2:
                start, stop, step = args[0], args[1], None
            else:
                start, stop, step = args
        self.writeline()
        self.write("for (%s=" % var)
        if start is None:
            self.write("0")
        else:
            self.visit(start)
        self.write("%s; %s%s" % (oneoff, var, cmp))
        if stop is None:
            self.write("0")
        else:
            self.visit(stop)
        self.write("; %s=%s%s" % (var, var, op))
        if step is None:
            self.write("1")
        else:
            self.visit(step)
        self.write(") begin")
        self.indent()
        self.visit(node.body)
        self.dedent()
        self.writeline()
        self.write("end")
        

    def visitFunction(self, node):
        raise AssertionError("To be implemented in subclass")

    def visitGetattr(self, node):
        assert isinstance(node.expr, ast.Name)
        assert node.expr.name in self.symdict
        obj = self.symdict[node.expr.name]
        if type(obj) is Signal:
            if node.attrname == 'next':
                self.isSigAss = True
            self.visit(node.expr)
        elif str(type(obj)) == "<class 'myhdl._enum.Enum'>":
            assert hasattr(obj, node.attrname)
            e = getattr(obj, node.attrname)
            self.write("%d'b%s" % (obj._nrbits, e._val))
        
    def visitIf(self, node):
        ifstring = "if ("
        for test, suite in node.tests:
            self.writeline()
            self.write(ifstring)
            self.visit(test)
            self.write(") begin")
            self.indent()
            self.visit(suite)
            self.dedent()
            self.writeline()
            self.write("end")
            ifstring = "else if ("
        if node.else_:
            self.writeline()
            self.write("else begin")
            self.indent()
            self.visit(node.else_)
            self.dedent()
            self.writeline()
            self.write("end")

    def visitInvert(self, node):
        self.write("(~")
        self.visit(node.expr)
        self.write(")")

    def visitKeyword(self, node):
        # XXX
        pass

    def visitLeftShift(self, node):
        self.binaryOp(node, '<<')

    def visitMod(self, node):
        self.write("(")
        self.visit(node.left)
        self.write(" % ")
        self.visit(node.right)
        self.write(")")
        
    def visitMul(self, node):
        self.binaryOp(node, '*')
       
    def visitName(self, node):
        if node.name in self.symdict:
            obj = self.symdict[node.name]
            if isinstance(obj, int):
                self.write(str(obj))
            elif type(obj) is Signal:
                self.write(obj._name)
            else:
                self.write(node.name)
        else:
            self.write(node.name)

    def visitNot(self, node):
        self.write("(!")
        self.visit(node.expr)
        self.write(")")
        
    def visitOr(self, node):
        self.multiOp(node, '||')

    def visitPass(self, node):
        # XXX
        pass
    
    def visitPower(self, node):
        # XXX
        pass
    
    def visitPrint(self, node):
        # XXX
        pass

    def visitPrintnl(self, node):
        # XXX
        pass
    
    def visitRaise(self, node):
        self.writeline()
        self.write('$display("Verilog: ')
        self.visit(node.expr1)
        self.write('");')
        self.writeline()
        self.write("$finish;")

    def visitRightShift(self, node):
        self.binaryOp(node, '>>')

    def visitSlice(self, node):
        # print dir(node)
        # print node.obj
        self.visit(node.expr)
        self.write("[")
        if node.lower is None:
            self.write("%s" % node.obj._len)
        else:
            self.visit(node.lower)
        self.write("-1:")
        if node.upper is None:
            self.write("0")
        else:
            self.visit(node.upper)
        self.write("]")

    def visitSliceObj(self, node):
        # XXX
        pass
            
    def visitSub(self, node):
        self.binaryOp(node, "-")

    def visitSubscript(self, node):
        self.visit(node.expr)
        self.write("[")
        assert len(node.subs) == 1
        self.visit(node.subs[0])
        self.write("]")

    def visitTuple(self, node):
        assert self.inYield
        tpl = node.nodes
        self.visit(tpl[0])
        for elt in tpl[1:]:
            self.write(" or ")
            self.visit(elt)
            
    def visitUnaryAdd(self, node, *args):
        # XXX
        pass
        
    def visitUnarySub(self, node, *args):
        # XXX
        pass

    def visitWhile(self, node):
        # XXX
        pass
        
    def visitYield(self, node):
        self.inYield = True
        self.writeline()
        self.write("@ (")
        self.visit(node.value)
        self.write(");")
        self.inYield = False

        
class _ConvertAlwaysVisitor(_ConvertGenVisitor):
    
    def __init__(self, *args):
        _ConvertGenVisitor.__init__(self, *args)

    def visitFunction(self, node):
        w = node.code.nodes[-1]
        assert isinstance(w.body.nodes[0], ast.Yield)
        sl = w.body.nodes[0].value
        self.inYield = True
        self.write("always @(")
        self.visit(sl)
        self.inYield = False
        self.write(") begin: %s" % self.name)
        self.indent()
        self.writeDeclarations()
        self.buf = self.codeBuf
        for s in w.body.nodes[1:]:
            self.visit(s)
        self.buf = self.fileBuf
        self.write(self.codeBuf.getvalue())
        self.dedent()
        self.writeline()
        self.write("end")
        self.writeline()
        self.writeline()
    
class _ConvertInitialVisitor(_ConvertGenVisitor):
    
    def __init__(self, *args):
        _ConvertGenVisitor.__init__(self, *args)

    def visitFunction(self, node):
        self.inYield = True
        self.write("initial begin: %s" % self.name) 
        self.indent()
        self.writeDeclarations()
        self.buf = self.codeBuf
        self.visit(node.code)
        self.buf = self.fileBuf
        self.write(self.codeBuf.getvalue())
        self.dedent()
        self.writeline()
        self.write("end")
        self.writeline()
        self.writeline()
    
         
        