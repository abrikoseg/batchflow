""" Monitoring (memory usage, cpu/gpu utilization) tools. """
import os
import time
from multiprocessing import Process, Manager, Queue
from contextlib import contextmanager

import psutil
import numpy as np
import matplotlib.pyplot as plt

try:
    import nvidia_smi
except ImportError:
    pass



class ResourceMonitor:
    """ Periodically runs supplied function in a separate process and stores its outputs.

    The created process runs infinitely until it is killed by SIGKILL signal.

    Parameters
    ----------
    function : callable
        Function to use. If not provided, defaults to the `get_usage` static method.
    frequency : number
        Periodicity of function calls in seconds.
    **kwargs
        Passed directly to `function` calls.

    Attributes
    ----------
    data : list
        Collected function outputs. Preserved between multiple runs.
    ticks : list
        Times of function calls. Preserved between multiple runs.
    """
    def __init__(self, function=None, frequency=0.1, **kwargs):
        self.function = function or self.get_usage
        self.frequency = frequency
        self.kwargs = kwargs

        self.pid = os.getpid()
        self.running = False

        self.stop_queue = None
        self.shared_list = None
        self.process = None

        self.start_time, self.prev_time, self.end_time = None, None, None
        self.ticks, self.data = [], []


    @staticmethod
    def endless_repeat(shared_list, stop_queue, function, frequency, **kwargs):
        """ Repeat `function` and storing results, until `stop` signal is recieved. """
        while stop_queue.empty():
            # As this process is killed ungracefully, it can be shut down in the middle of data appending.
            # We let Python handle it by ignoring the exception.
            try:
                shared_list.append(function(**kwargs))
            except (BrokenPipeError, ConnectionResetError):
                pass
            time.sleep(frequency)

    def start(self):
        """ Start a separate process with function calls every `frequency` seconds. """
        self.running = True
        manager = Manager()
        self.shared_list = manager.list()
        self.stop_queue = Queue()

        self.start_time = time.time()
        self.prev_time = self.start_time

        args = self.shared_list, self.stop_queue, self.function, self.frequency
        self.process = Process(target=self.endless_repeat, args=args, kwargs={'pid': self.pid, **self.kwargs})
        self.process.start()

    def fetch(self):
        """ Append collected data to the instance attributes. """
        n = len(self.data)
        # We copy data so additional points don't appear during this function execution
        self.data = self.shared_list[:]
        self.end_time = time.time()

        # Compute one more entry
        point = self.function(pid=self.pid, **self.kwargs)
        tick = time.time()

        # Update timestamps, append additional entries everywhere
        # If data was appended to `shared_list` during the execution of this function, the order might be wrong;
        # But, as it would mean that the time between calls to `self.function` is very small, it is negligeable.
        self.ticks.extend(np.linspace(self.prev_time, self.end_time, num=len(self.data) - n).tolist())
        self.data.append(point)
        self.shared_list.append(point)
        self.ticks.append(tick)

        self.prev_time = time.time()

    def stop(self):
        """ Stop separate process. """
        self.stop_queue.put(True)
        self.process.join()
        self.running = False


    def visualize(self):
        """ Simple plots of collected data-points. """
        plt.figure(figsize=(8, 6))
        plt.plot(np.array(self.ticks) - self.ticks[0], self.data)
        plt.title(self.__class__.__name__)
        plt.xlabel('Time, s', fontsize=12)
        plt.ylabel(self.UNIT, fontsize=12, rotation='horizontal', labelpad=15)
        plt.grid(True)
        plt.show()



class CPUMonitor(ResourceMonitor):
    """ Track CPU usage. """
    UNIT = '%'

    @staticmethod
    def get_usage(**kwargs):
        """ Track CPU usage. """
        _ = kwargs
        return psutil.cpu_percent()


class MemoryMonitor(ResourceMonitor):
    """ Track total virtual memory usage. """
    UNIT = 'Gb'

    @staticmethod
    def get_usage(**kwargs):
        """ Track total virtual memory usage. """
        _ = kwargs
        return psutil.virtual_memory().used / (1024 **3)


class RSSMonitor(ResourceMonitor):
    """ Track non-swapped physical memory usage. """
    UNIT = 'Gb'

    @staticmethod
    def get_usage(pid=None, **kwargs):
        """ Track non-swapped physical memory usage. """
        _ = kwargs
        process = psutil.Process(pid)
        return process.memory_info().rss / (1024 ** 2) # mbytes


class VMSMonitor(ResourceMonitor):
    """ Track current process virtual memory usage. """
    UNIT = 'Gb'

    @staticmethod
    def get_usage(pid=None, **kwargs):
        """ Track current process virtual memory usage. """
        _ = kwargs
        process = psutil.Process(pid)
        return process.memory_info().vms / (1024 ** 3) # gbytes


class USSMonitor(ResourceMonitor):
    """ Track current process unique virtual memory usage. """
    UNIT = 'Gb'

    @staticmethod
    def get_usage(pid=None, **kwargs):
        """ Track current process unique virtual memory usage. """
        _ = kwargs
        process = psutil.Process(pid)
        return process.memory_full_info().uss / (1024 ** 3) # gbytes


class GPUMonitor(ResourceMonitor):
    """ Track GPU usage. """
    UNIT = '%'

    @staticmethod
    def get_usage(gpu_list=None, **kwargs):
        """ Track GPU usage. """
        _ = kwargs
        gpu_list = gpu_list or [0]
        nvidia_smi.nvmlInit()
        handle = [nvidia_smi.nvmlDeviceGetHandleByIndex(i) for i in gpu_list]
        res = [nvidia_smi.nvmlDeviceGetUtilizationRates(item) for item in handle]
        return [item.gpu for item in res]


class GPUMemoryMonitor(ResourceMonitor):
    """ Track GPU memory usage. """
    UNIT = '%'

    @staticmethod
    def get_usage(gpu_list=None, **kwargs):
        """ Track GPU memory usage. """
        _ = kwargs
        gpu_list = gpu_list or [0]
        nvidia_smi.nvmlInit()
        handle = [nvidia_smi.nvmlDeviceGetHandleByIndex(i) for i in gpu_list]
        res = [nvidia_smi.nvmlDeviceGetUtilizationRates(item) for item in handle]
        return [item.memory for item in res]



MONITOR_ALIASES = {
    MemoryMonitor: ['mmonitor', 'memory', 'memorymonitor'],
    CPUMonitor: ['cmonitor', 'cpu', 'cpumonitor'],
    RSSMonitor: ['rss'],
    VMSMonitor: ['vms'],
    USSMonitor: ['uss'],
    GPUMonitor: ['gpu'],
    GPUMemoryMonitor: ['gpu_memory'],
}

MONITOR_ALIASES = {alias: monitor for monitor, aliases in MONITOR_ALIASES.items()
                   for alias in aliases}


@contextmanager
def monitor_resource(resource='memory', frequency=0.5, **kwargs):
    """ A convenient context manager to profile a part of code. Can use one or more monitors. """
    resource = [resource] if not isinstance(resource, (tuple, list)) else resource
    monitors = [MONITOR_ALIASES[res.lower()](frequency=frequency, **kwargs) if isinstance(res, str) else res
                for res in resource]

    try:
        for monitor in monitors:
            monitor.start()
        yield monitors[0] if len(monitors) == 1 else monitors
    finally:
        for monitor in monitors:
            monitor.fetch()
            monitor.stop()


def monitor_memory(frequency=0.5):
    return monitor_resource('memory', frequency=frequency)

def monitor_cpu(frequency=0.5):
    return monitor_resource('cpu', frequency=frequency)

def monitor_gpu(frequency=0.5, gpu_list=None):
    return monitor_resource('gpu', frequency=frequency, gpu_list=gpu_list)
