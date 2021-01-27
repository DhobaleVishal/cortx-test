#
# Copyright (c) 2020 Seagate Technology LLC and/or its Affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For any questions about this software or licensing,
# please email opensource@seagate.com or cortx-questions@seagate.com.
#
# -*- coding: utf-8 -*-
# !/usr/bin/python

import logging
import queue
import threading
from threading import Thread
from commons.constants import NWORKERS

logger = logging.getLogger(__name__)


class WorkQ(queue.Queue):
    def __init__(self, func, maxsize):
        self.lock_req = maxsize != 0
        self.func = func
        self.semaphore = threading.Semaphore(maxsize)
        queue.Queue.__init__(self, maxsize)

    def put(self, item):
        if self.lock_req:
            self.semaphore.acquire()
        queue.Queue.put(self, item)

    def task_done(self):
        if self.lock_req:
            self.semaphore.release()
        queue.Queue.task_done(self)


class Workers(object):
    """ A fixed size thread pool for I/O bound tasks """
    def start_workers(self, nworkers=NWORKERS, func=None):
        self.w_workq = WorkQ(func, nworkers)
        self.w_workers = []
        for i in range(nworkers):
            w = Thread(target=self.worker)
            w.start()
            self.w_workers.append(w)

    def worker(self):
        while True:
            wq = self.w_workq.get()
            if wq is None:
                self.w_workq.task_done()
                break
            wi = wq.get()
            wq.func(wi)
            wq.task_done()
            self.w_workq.task_done()

    def wenque(self, item):
        self.w_workq.put(item)

    def end_workers(self):
        for i in range(len(self.w_workers)):
            self.w_workq.put(None)
        self.w_workq.join()
        logger.info('shutdown all workers')
        logger.info('Joining all threads to main thread')
        for i in range(len(self.w_workers)):
            self.w_workers[i].join()