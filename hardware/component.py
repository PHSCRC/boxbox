#
# Base classes for GPIO hardware
#

import RPi.GPIO as gpio
import time, os

from collections import defaultdict
from collections.abc import Iterable
from threading import Thread

from traceback import print_exc, format_exc

def delay(microseconds):
    time.sleep(microseconds/1000000)

def waitms(milliseconds):
    time.sleep(milliseconds/1000)

BASE_DIR = os.environ.get("IO_BASE_DIR","/var/run/")

class FIFOFile:
    def __init__(self, fn, num=0):
        self.__filename = os.path.join(BASE_DIR, fn + str(num))
        try:
            os.unlink(self.__filename)
        except FileNotFoundError:
            pass
        umask = os.umask(0)
        os.mkfifo(self.__filename)
        self.__fd = open(os.open(self.__filename, os.O_RDWR|os.O_NONBLOCK), "wb+", 0)
        os.umask(umask)

    def close(self):
        self.__fd.close()
        try:
            os.unlink(self.__filename)
        except FileNotFoundError:
            pass
        
    def __getattr__(self, name):
        return getattr(self.__fd, name)
        
class Component:
    def __init__(self, fn=None, numchannels=1, offset=0):
        super().__init__()
        self.__fn = fn if fn else self._FN
        self.__channels = range(offset, numchannels + offset)
        self.__readdata = defaultdict(bytes)
        self.__initialized = False

    def writedata(self, data, channel=0):
        if hasattr(data, "__iter__"):
            data = ",".join([str(i) for i in data])
        if not isinstance(data, bytes):
            data = str(data).encode()
        self.__fifos[channel].write(data + b"\n")

    def readdata(self, channel=0):
        data = self.__fifos[channel].read()
        if data:
            self.__readdata[channel] += data
        if b"\n" in self.__readdata[channel]:
            data, newline, self.__readdata[channel] = self.__readdata[channel].partition(b"\n")
            text = data.decode()
            if "," in text:
                return tuple([float(i) for i in text.split(",")])
            else:
                return float(text)
        else:
            return None
        
    def _checkInit(self,quiet=False):
        if quiet:
            return self.__initialized
        if not self.__initialized:
            raise RuntimeError("This {} has not been initialized yet!".format(
                    self.__class__.__name__))

    def _set_init(self):
        self.__initialized = True

    def init(self):
        if len(self.__channels) < 2:
            self.__fifos = [FIFOFile(self.__fn)]
        else:
            self.__fifos = []
            for i in self.__channels:
                self.__fifos.append(FIFOFile(self.__fn, i))

    def cleanup(self):
        for i in self.__fifos:
            i.close()
        self.__initialized = False

    def __enter__(self):
        self.init()
        return self

    def __exit__(self, type, value, tb):
        self.cleanup()

class GPIOComponent(Component):
    def __init__(self, outpins=(), inpins=(), *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__out_pins = outpins
        self.__in_pins = inpins

    def init(self):
        super().init()
        if self._checkInit(True):
            try:
                self.cleanup()
            finally:
                pass
            
        gpio.setmode(gpio.BCM)

        for ch in self.__out_pins:
            try:
                gpio.setup(ch, gpio.OUT, initial=gpio.LOW)
            except ValueError as err:
                print("Error setting up pin {}. ({})".format(ch, repr(err)))
        for ch in self.__in_pins:
            try:
                gpio.setup(ch, gpio.IN)
            except ValueError as err:
                print("Error setting up pin {}. ({})".format(ch, repr(err)))

        self._set_init()

    def cleanup(self):
        if not self._checkInit(True):
            return False
        super().cleanup()
        for ch in self.__out_pins + self.__in_pins:
            gpio.cleanup(ch)
        return True

class I2CComponent(Component):
    def __init__(self, addr=None, *args, **kwargs):
        self._address = addr
        super().__init__(*args, **kwargs)

class EventedInput:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__next_id = -1
        self.__handlers = defaultdict(dict)

    def _get_handlers(self,pin=None,generic=False):
        if (pin is None) and (not generic):
            raise TypeError("Must supply pin for non generic handler!")
        return self.__handlers["generic"] if generic else self.__handlers[pin]

    def add_handler(self,callback,pin=None,generic=False):
        self.__next_id += 1
        self._get_handlers(pin,generic)[self.__next_id] = callback
        return self.__next_id

    def remove_handler(self,hid,pin=None,generic=False):
        del self._get_handlers(pin,generic)[hid]

    def _handle_pin(self,pin):
        for i in (list(self._get_handlers(generic=True).values())
                  + list(self._get_handlers(pin).values())):
            try:
                i(pin)
            except Exception as e:
                print_exc()

class LoopedComponent:
    """Subclasses must implement a tick method and define _mswait"""
    def __init__(self, *args, **kwargs):        
        super().__init__(*args, **kwargs)
        self.thread = Thread(target=self.runloop)
        self.thread.daemon = True
        self.__started = False
        self.thread.start()

    def init(self, autostart=False):
        super().init()
        self._set_init()
        if autostart:
            self.start()
        
    def cleanup(self):
        if self.__started:
            self.stop()
        super().cleanup()

    def start(self):
        self._checkInit()
        self.__started = True

    def stop(self):
        self.__started = False

    def runloop(self):
        while True:
            if self.__started:
                self.tick()
            waitms(self._mswait)
