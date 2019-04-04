# (C) 2017 University of Bristol. See License.txt

from Compiler.types import MemValue, read_mem_value, regint, Array
from Compiler.program import Tape, Program
from Compiler.instructions_gc import *
import operator

# Logic copied from AG-MPC
# https://github.com/emp-toolkit/emp-tool/blob/stable/emp-tool/circuits/integer.hpp
#
# Note that this is an integer representation, not modulo p

class bits(object):
    global_gid = 0
    
    def __init__(self, input_party=-1):
        self.gid = None
        if input_party > -1:
            self.input_party = input_party
            self.set_gid()
            if input_party not in program_gc.input_wires.keys():
                program_gc.input_wires[input_party] = []
            program_gc.input_wires[input_party].append(self.gid)
            program_gc.total_inputs += 1

    def set_gid(self):
        self.gid = bits.global_gid
        bits.global_gid += 1
        program_gc.total_wires += 1

    def __str__(self):
        return str(self.gid)

class cbits(bits):
    def __init__(self, value):
        super(cbits, self).__init__()
        if (value != 0) and (value != 1):
            raise ValueError("cbits must have a value of 0 or 1")
        self.value = value

    def __invert__(self):
        return cbits((self.value + 1) % 2)

    def __xor__(self, other):
        if isinstance(other, cbits):
            return cbits(self.value ^ other.value)
        else:
            return NotImplemented

    def __and__(self, other):
        if isinstance(other, cbits):
            return cbits(self.value & other.value)
        else:
            return NotImplemented

    def __str__(self):
        return str("{}: v = {}".format(self.gid, self.value))

    __rxor__ = __xor__
    __rand__ = __and__
    
            
class sbits(bits):
    def __init__(self, input_party=-1):
        super(sbits, self).__init__(input_party)
    
    def test_instance(self, other):
        if not isinstance(other, sbits):
            raise ValueError("requires type sbits")

    def _and(self, other):
        res = sbits()
        and_gc(res, self, other)
        res.set_gid()
        return res

    def _xor(self, other):
        res = sbits()
        xor_gc(res, self, other)
        res.set_gid()
        return res

    def _invert(self):
        res = sbits()
        invert_gc(res, self)
        res.set_gid()
        return res
    
    def __invert__(self):
        return self._invert()

    def __xor__(self, other):
        if isinstance(other, cbits):
            if other.value == 0:
                return self
            else:
                return self.__invert__()
        elif isinstance(other, sbits):
            return self._xor(other)
        else:
            return NotImplemented

    def __and__(self, other):
        if isinstance(other, cbits):
            if other.value == 0:
                return cbits(0)
            else:
                return self
        elif isinstance(other, sbits):
            return self._and(other)
        else:
            return NotImplemented

    def __or__(self, other):
        a = self & other
        b = self ^ other
        c = b ^ a
        return c

    __rxor__ = __xor__
    __rand__ = __and__
    __ror__ = __or__

    def __eq__(self, other):
        return ~(self ^ other)

def add_full(dest, op1, op2, size, carry_in=None, carry_out=None):
    if size == 0:
        if carry_in and carry_out:
            return carry_in
        else:
            return None

    carry = carry_in
    if carry is None:
        carry = cbits(0)

    skip_last = int(carry_out == None)

    i = 0
    while (size > skip_last):
        axc = op1[i] ^ carry
        bxc = op2[i] ^ carry
        dest[i] = op1[i] ^ bxc
        t = axc & bxc
        carry = carry ^ t
        i += 1
        size -= 1

    if carry_out is None:
        dest[i] = carry ^ op2[i] ^ op1[i]
        return None
    else:
        # return carry out, since we cannot assign that value in this function
        return carry

def sub_full(dest, op1, op2, size, borrow_in=None, borrow_out=None):
    if size == 0:
        if borrow_in and borrow_out:
            return borrow_in
        else:
            return None

    borrow = borrow_in
    if borrow is None:
        borrow = cbits(0)

    skip_last = int(borrow_out == None)

    i = 0
    while size > skip_last:
        bxa = op1[i] ^ op2[i]
        bxc = borrow ^ op2[i]
        dest[i] = bxa ^ borrow
        t = bxa & bxc
        borrow = borrow ^ t

        i += 1
        size -= 1

    if borrow_out is None:
        dest[i] = op1[i] ^ op2[i] ^ borrow
        return None
    else:
        return borrow

def mul_full(dest, op1, op2, size):
    s = []
    t = []

    for i in range(0, size):
        s.append(cbits(0))
        t.append(None)

    for i in range(0, size):
        for k in range(0, size - i):
            t[k] = op1[k] & op2[i]
        s2 = [bits() for j in range(size - i)]
        add_full(s2, s[i:], t, size - i)
        for j in range(0, size - i):
            s[i + j] = s2[j]
        
    for i in range(0, size):
        dest[i] = s[i]

def if_then_else(dest, tsrc, fsrc, size, cond):
    i = 0
    s = size
    while (s):
        x = tsrc[i] ^ fsrc[i]
        a = cond & x
        dest[i] = a ^ fsrc[i]
        i += 1
        s -= 1

def cond_neg(cond, dest, src, size):
    c = cond

    i = 0
    for j in range(0, size - 1):
        dest[i] = src[i] ^ cond
        t = dest[i] ^ c
        c = c & dest[i]
        dest[i] = t
        i += 1

    dest[i] = cond ^ c ^ src[i]

def div_full(vquot, op1, op2, size, vrem=None):
    overflow = [sbits()] * size
    temp = [sbits()] * size
    rem = [sbits()] * size
    quot = [sbits()] * size
    b = sbits()

    for i in range(0, size):
        rem[i] = op1[i]

    overflow[0] = cbits(0)

    for i in range(1, size):
        overflow[i] = overflow[i - 1] | op2[size - i]
    for i in range(size - 1, -1, -1):
        b = sub_full(temp, rem[i:], op2, size-i, borrow_out=b)
        b = b | overflow[i]
        
        rem_temp = [sbits() for j in range(i, size)]
        if_then_else(rem_temp, rem[i:], temp, size-i, b)
        for j in range(i, size):
            rem[j] = rem_temp[j-i]
        
        quot[i] = ~b

    for i in range(0, size):
        if vrem is not None:
            vrem[i] = rem[i]
        vquot[i] = quot[i]

# index 0 is the MOST significant bit
class int_gc(object):
    value_type = sbits
    def __init__(self, length):
        self.length = length
        self.bits = [bits() for i in range(length)]

    def test_instance(self, other):
        if not (isinstance(other, (int_gc))):
            raise ValueError("Type {} not supported with integer".format(type(other)))
        if not (self.length == other.length):
            raise ValueError("Integer lengths must match")

    def __and__(self, other):
        self.test_instance(other)
        dest = int_gc(self.length)
        for i in range(self.length):
            dest.bits[i] = self.bits[i] & other.bits[i]
        return dest
            
    def __xor__(self, other):
        self.test_instance(other)
        dest = int_gc(self.length)
        for i in range(self.length):
            dest.bits[i] = self.bits[i] ^ other.bits[i]
        return dest

    # This function shouldn't be called
    def __invert__(self):
        assert(False)

    def __add__(self, other):
        self.test_instance(other)
        dest = int_gc(self.length)
        add_full(dest.bits, self.bits, other.bits, self.length)
        return dest

    def __sub__(self, other):
        self.test_instance(other)
        dest = int_gc(self.length)
        sub_full(dest.bits, self.bits, other.bits, self.length)
        return dest

    def __neg__(self):
        dest = int_gc(self.length)
        dest.bits = [b for b in self.bits]
        b[0] = ~b[0]
        return dest

    def absolute(self):
        dest = int_gc(self.length)
        for i in range(0, self.length):
            dest.bits[i] = self.bits[self.length-1]
        return (self + dest) ^ dest

    def __mul__(self, other):
        self.test_instance(other)
        dest = int_gc(self.length)
        mul_full(dest.bits, self.bits, other.bits, self.length)
        return dest

    def __div__(self, other):
        self.test_instance(other)
        dest = int_gc(self.length)
        i1 = self.absolute()
        i2 = other.absolute()
        sign = self.bits[other.length - 1] ^ other.bits[other.length - 1]
        div_full(dest.bits, i1.bits, i2.bits, self.length)
        dest_temp = [int_gc() for i in range(self.length)]
	cond_neg(sign, dest_temp, dest.bits, self.length)
        for i in range(len(dest_temp)):
            dest.bits[i] = dest_temp[i]
        return dest

    def __ge__(self, other):
        self.test_instance(other)
        res = self - other
        ret = ~res.bits[self.length - 1]
        return ret

    def __lt__(self, other):
        return ~(self >= other)

    def __le__(self, other):
        return (other >= self)

    def __gt__(self, other):
        return ~(self <= other)

    def __eq__(self, other):
        self.test_instance(other)
        r = cbits(1)
        for i in range(0, op1.length):
            r = r & (op1.bits[i] == op2.bits[i])
        return r

    def __ne__(self, other):
        return ~(self == other)

    def __lshift__(self, other):
        if not isinstance(other, int):
            raise ValueError("Shift amount must be an integer!")

        if other == 0:
            return self

        dest = int_gc(self.length)
        if other > self.length:
            dest.bits = [cbits(0) for i in range(self.length)]
        else:
            for i in range(self.length - 1, other-1, -1):
                dest.bits[i] = self.bits[i-other]
            for i in range(other - 1, -1, -1):
                dest.bits[i] = cbits(0)
        return dest
                
    def __rshift__(self, other):
        if not isinstance(other, int):
            raise ValueError("Shift amount must be an integer!")

        if other == 0:
            return self

        dest = int_gc(self.length)
        if other > self.length:
            dest.bits = [cbits(0) for i in range(self.length)]
        else:
            for i in range(other, self.length):
                dest.bits[i-other] = self.bits[i]
            for i in range(0, self.length-other):
                dest.bits[i] = cbits(0)

        return dest

    # If new_length > current length, convert appends 0s in the more significant positions
    # Otherwise, it will truncate the most significant bits
    def convert(self, new_length):
        if new_length == self.length:
            return self
        elif new_length > self.length:
            dest = int_gc(new_length)
            for i in range(0, new_length - self.length):
                dest.bits[i] = cbits(0)
            for i in range(new_length - self.length, self.length):
                dest.bits[i] = self.bits[i]
        else:
            dest = int_gc(new_length)
            dest.bits = [self.bits[i] for i in range(new_length, self.length)]
        return dest
        
    def reveal(self):
        for b in self.bits:
            if isinstance(b, sbits):
                program_gc.output_wires.append(b.gid)
            
    def __str__(self):
        s = ""
        for i in range(len(self.bits)):
            s += "{}: {}\n".format(i, self.bits[i])
        return s


class cint_gc(int_gc):
    value_type = cbits
    def __init__(self, length, value=None):
        assert(length > 0)
        self.bits = []
        self.length = length
        
        if value is None:
            self.bits = [cbits(0) for i in range(self.length)]
        else:
            s = bin(value)[2:]
            if len(s) > self.length:
                s = s[len(s) - self.length:len(s)]
            if len(s) < self.length:
                for i in range(self.length - len(s)):
                    s = "0" + s
            self.bits = [cbits(int(x)) for x in s]

    def get_decimal(self):
        value = 0
        idx = len(self.bits) - 1
        for b in self.bits:
            if b.value == 1:
                value += (b.value << idx)
            idx -= 1
        return value

    def preprocess(self, other):
        if isinstance(other, cint_gc):
            self_value = self.get_decimal()
            other_value = other.get_decimal()
            return (self_value, other_value)
        else:
            return NotImplemented

    def __and__(self, other):
        ret = self.preprocess(other)
        if ret is NotImplemented:
            return ret
        (v1, v2) = ret
        return cint_gc(self.length, v1 & v2)

    def __xor__(self, other):
        ret = self.preprocess(other)
        if ret is NotImplemented:
            return ret
        (v1, v2) = ret
        return cint_gc(self.length, v1 ^ v2)

    def __add__(self, other):
        ret = self.preprocess(other)
        if ret is NotImplemented:
            return other.__add__(self)
        (v1, v2) = ret
        return cint_gc(self.length, v1 + v2)

    def __sub__(self, other):
        ret = self.preprocess(other)
        if ret is NotImplemented:
            return other.__sub__(self, reverse=True)
        (v1, v2) = ret
        return cint_gc(self.length, v1 - v2)

    def absolute(self):
        value = self.get_decimal()
        return cint_gc(abs(value))

    def __mul__(self, other):
        ret = self.preprocess(other)
        if ret is NotImplemented:
            return other.__mul__(self)
        (v1, v2) = ret
        return cint_gc(self.length, v1 * v2)

    def __div__(self, other):
        ret = self.preprocess(other)
        if ret is NotImplemented:
            return other.__div__(self, reverse=True)
        (v1, v2) = ret
        return cint_gc(self.length, v1 / v2)

    def __lshift__(self, other):
        if not isinstance(other, int):
            raise ValueError("Shift value must be an integer")
        v1 = self.get_decimal()
        v1 = v1 << other
        return cint_gc(self.length, v1)
    
    def __rshift__(self, other):
        if not isinstance(other, int):
            raise ValueError("Shift value must be an integer")
        v1 = self.get_decimal()
        v1 = v1 >> other
        return cint_gc(self.length, v1)

    def __eq__(self, other):
        ret = self.preprocess(other)
        if ret is NotImplemented:
            return other.__eq__(self)
        (v1, v2) = ret
        return cint_gc(1, int(v1 == v2))
    
    def __ne__(self, other):
        return ~(self == other)

    def __gt__(self, other):
        ret = self.preprocess(other)
        if ret is NotImplemented:
            return other.__leq__(self)
        (v1, v2) = ret
        return cint_gc(1, int(v1 > v2))

class sint_gc(int_gc):
    value_type = sbits
    def __init__(self, length, input_party=-1):
        assert(length > 0)
        self.length = length
        self.bits = [sbits(input_party) for i in range(length)]

# This is a wrapper around sint
# Implementes fixed point logic
# f = number of bits in the decimal position
# k = total number of bits
class sfix_gc(object):
    __slots__ = ['v', 'f', 'k', 'size']
            
    @classmethod
    def set_precision(cls, f, k=None):
        cls.f = f
        if k is None:
            cls.k = 2 * f
        else:
            cls.k = k

    def __init__(self, v=None, input_party=-1):
        if v is not None and isinstance(v, int_gc):
            self.v = v << sfix_gc.f
        else:
            self.v = sint_gc(sfix_gc.k, input_party)

    def load_sint(self, v):
        self.v = v

    def __add__(self, other):
        if isinstance(other, sfix_gc):
            intv = self.v + other.v
            return sfix_gc(v=intv)
        elif isinstance(other, sint_gc):
            other_fix = sfix_gc(v=other, scale=True)
            return (self + other_fix)
        else:
            return NotImplemented
    
    def __sub__(self, other):
        if isinstance(other, sfix_gc):
            intv = self.v - other.v
            return sfix_gc(v=intv)
        elif isinstance(other, sint_gc):
            other_fix = sfix_gc(v=other, scale=True)
            return (self - other_fix)
        else:
            return NotImplemented

    def __mul__(self, other):
        if isinstance(other, sfix_gc):
            v_ex = self.v.convert(sfix_gc.k * 2)
            ov_ex = other.v.convert(sfix_gc.k * 2)
            ret_v = v_ex * ov_ex
            ret_v = ret_v >> sfix_gc.f
            ret_v = ret_v.convert(sfix_gc.k)
            ret = sfix_gc()
            ret.load_int(ret_v)
            return ret
        elif isinstance(other, sint_gc):
            ret_v = v_ex * other
            ret = sfix_gc()
            ret.load_int(ret_v)
            return ret       
        else:
            return NotImplemented

    def __div__(self, other):
        if isinstance(other, sfix_gc):
            v_ex = self.v.convert(sfix_gc.k * 2)
            ov_ex = other.v.convert(sfix_gc.k * 2)
            ret_v = v_ex / ov_ex
            ret_v = ret_v >> sfix_gc.f
            ret_v = ret_v.convert(sfix_gc.k)
            ret = sfix_gc()
            ret.load_int(ret_v)
            return ret
        elif isinstance(other, sint_gc):
            ret_v = v_ex / sint_gc
            ret = sfix_gc()
            ret.load_int(ret_v)
            return ret       
        else:
            return NotImplemented

    def __eq__(self, other):
        if isinstance(other, sfix_gc):
            return sint_gc(self.v == other.v)
        elif isinstance(other, sint_gc):
            other_fix = sfix_gc(other.length, other)
            return (self == other_fix)
        else:
            return NotImplemented

    def __ne__(self, other):
        return ~(self == other)

    def __ge__(self, other):
        if isinstance(other, sfix_gc):
            return (self.v >= other.v)
        elif isinstance(other, sint_gc):
            other_fix = sfix_gc(other.length, other)
            return (self >= other_fix)
        else:
            return NotImplemented

    def __lt__(self, other):
        return ~(self >= other)

    def __le__(self, other):
        return (other >= self)

    def __gt__(self, other):
        return ~(self <= other)

class ArrayGC(object):
    def __init__(self, length):
        self.length = length
        self.data = [None for i in range(length)]

    def __getitem__(self, index):
        return self.data[index]

    def __setitem__(self, index, value):
        self.data[index] = value

class MatrixGC(object):
    def __init__(self, rows, columns):
        self.rows = rows
        self.columns = columns
        self.data = [ArrayGC(columns) for r in range(rows)]

    def __getitem__(self, index):
        return self.data[index]

class cintArrayGC(ArrayGC):
    def __init__(self, length):
        super(self, cintArrayGC).__init__(length)
        self.data = [cint_gc(0) for i in range(length)]

class cintMatrixGC(MatrixGC):
    def __init__(self, rows, columns):
        super(cintMatrixGC, self).__init__(rows, columns)
        self.data = [cintArrayGC(columns) for i in range(rows)]

class sintArrayGC(ArrayGC):
    def __init__(self, length):
        super(sintArrayGC, self).__init__(length)
        self.data = [sint_gc(32) for i in range(length)]

class sintMatrixGC(MatrixGC):
    def __init__(self, rows, columns):
        super(sintMatrixGC, self).__init__(rows, columns)
        self.data = [sintArrayGC(columns) for i in range(rows)]

class sfixArrayGC(ArrayGC):
    def __init__(self, length):
        super(sfixArrayGC, self).__init__(length)
        self.data = [sfix_gc(0) for i in range(length)]

class sfixMatrixGC(MatrixGC):
    def __init__(self, rows, columns):
        super(sfixMatrixGC, self).__init__(rows, columns)
        self.data = [sfixArrayGC(columns) for i in range(rows)]