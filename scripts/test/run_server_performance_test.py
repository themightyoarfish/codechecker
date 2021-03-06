# -----------------------------------------------------------------------------
#                     The CodeChecker Infrastructure
#   This file is distributed under the University of Illinois Open Source
#   License. See LICENSE.TXT for details.
# -----------------------------------------------------------------------------
"""
Performance tester for the server.
"""
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import

import argparse
import csv
import datetime
import math
import os
import random
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict


VERBOSE = False
FINISH = False
PROCESSES = []


def return_duration(func):
    """
    This decorator makes the applied function return its original return value
    and its run duration respectively in a tuple.
    """
    def func_wrapper(*args, **kwargs):
        before = datetime.datetime.now()
        ret = func(*args, **kwargs)
        after = datetime.datetime.now()
        return ret, (after - before).total_seconds()

    return func_wrapper


def print_process_output(message, stdout, stderr):
    global VERBOSE

    if not VERBOSE:
        return

    print(message)
    print('-' * 20 + 'stdout' + '-' * 20)
    print(stdout)
    print('-' * 20 + 'stderr' + '-' * 20)
    print(stderr)
    print('-' * (40 + len('stdout')))


def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Performance tester for CodeChecker storage.',
        epilog='This test simulates some user actions which are performed on '
               'a CodeChecker server. The test instantiates the given number '
               'of users. These users perform a run storage, some queries and '
               'run deletion. The duration of all tasks is measured. These '
               'durations are written to the output file at the end of the '
               'test in CSV format. The tasks are performed for all report '
               'directories by all users.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('input',
                        type=str,
                        metavar='file/folder',
                        nargs='+',
                        default='~/.codechecker/reports',
                        help="The analysis result files and/or folders.")
    parser.add_argument('--url',
                        type=str,
                        metavar='PRODUCT_URL',
                        dest='product_url',
                        default='localhost:8001/Default',
                        required=False,
                        help="The URL of the product to store the results "
                             "for, in the format of host:port/ProductName.")
    parser.add_argument('-o', '--output',
                        type=str,
                        required=True,
                        help="Output file name for printing statistics.")
    parser.add_argument('-u', '--users',
                        type=int,
                        default=1,
                        help="Number of users")
    parser.add_argument('-t', '--timeout',
                        type=int,
                        default=-1,
                        help="Timout in seconds. The script stops when the "
                             "timeout expires. If a negative number is given "
                             "then the script runs until it's interrupted.")
    parser.add_argument('-r', '--rounds',
                        type=int,
                        default=-1,
                        help="The user(s) will accomplist their jobs this "
                             "many times.")
    parser.add_argument('-b', '--beta',
                        type=int,
                        default=10,
                        help="In the test users are waiting a random amount "
                             "of seconds. The random numbers have exponential "
                             "distribution of the beta parameter can be "
                             "provided here.")
    parser.add_argument('-v', '--verbose',
                        action='store_true',
                        help="Print the output of CodeChecker commands.")

    return parser.parse_args()


class StatManager(object):
    """
    This class stores the statistics of the single user events and prints them
    in CSV format. To produce a nice output the users should do the same tasks
    in the same order, e.g. they should all store, query and delete a run
    in this order. In the output table a row belongs to each user. The columns
    are the durations of the accomplished tasks.
    """

    def __init__(self):
        # In this dictionary user ID is mapped to a list of key-value
        # pairs: the key is a process name the value is its duration.
        self._stats = defaultdict(list)

    def add_duration(self, user_id, task_name, duration):
        """
        Add the duration of an event to the statistics.
        """
        self._stats[user_id].append((task_name, duration))

    def print_stats(self, file_name):
        if not self._stats:
            return

        with open(file_name, 'w') as f:
            writer = csv.writer(f)

            longest = []
            for _, durations in self._stats.items():
                if len(durations) > len(longest):
                    longest = durations

            header = ['User'] + map(lambda x: x[0], longest)

            writer.writerow(header)

            for user_id, durations in self._stats.iteritems():
                writer.writerow([user_id] + map(lambda x: x[1], durations))


class UserSimulator(object):
    """
    This class simulates a user who performs actions one after the other. The
    durations of the single actions are stored in the statistics.
    """

    _counter = 0

    def __init__(self, stat, beta):
        UserSimulator._counter += 1
        self._id = UserSimulator._counter
        self._actions = list()
        self._stat = stat
        self._beta = beta

    def get_id(self):
        return self._id

    def add_action(self, name, func, args):
        """
        This function adds a user action to be played later.
        name -- The name of the action to identify it in the statistics output.
        func -- A function object on which @return_duration decorator is
                applied.
        args -- A tuple of function arguments to be passed to func.
        """
        self._actions.append((name, func, args))

    def play(self):
        global FINISH

        for name, func, args in self._actions:
            if FINISH:
                break

            self._user_random_sleep()
            ret, duration = func(*args)
            self._stat.add_duration(self._id, name, duration)

            if ret != 0:
                sys.exit("{} job has failed".format(name))

    def _user_random_sleep(self):
        sec = -self._beta * math.log(1.0 - random.random())
        print("User {} is sleeping {} seconds".format(self._id, sec))
        time.sleep(sec)


@return_duration
def store_report_dir(report_dir, run_name, server_url):
    print("Storage of {} is started ({})".format(run_name, report_dir))

    store_process = subprocess.Popen([
        'CodeChecker', 'store',
        '--url', server_url,
        '--name', run_name,
        report_dir],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)

    global PROCESSES
    PROCESSES.append(store_process)

    print_process_output("Output of storage",
                         *store_process.communicate())

    print("Storage of {} {}".format(
        run_name,
        "is done" if store_process.returncode == 0 else "failed"))

    return store_process.returncode


@return_duration
def local_compare(report_dir, run_name, server_url):
    print("Local compare of {} is started ({})".format(run_name, report_dir))

    compare_process = subprocess.Popen([
        'CodeChecker', 'cmd', 'diff',
        '--url', server_url,
        '-b', run_name,
        '-n', report_dir,
        '--unresolved'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)

    global PROCESSES
    PROCESSES.append(compare_process)

    print_process_output("Output of local compare",
                         *compare_process.communicate())

    print("Local compare of {} {}".format(
        run_name,
        "is done" if compare_process.returncode == 0 else "failed"))

    return compare_process.returncode


@return_duration
def get_reports(run_name, server_url):
    print("Getting report list for {} is started".format(run_name))

    report_process = subprocess.Popen([
        'CodeChecker', 'cmd', 'results',
        '--url', server_url,
        run_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)

    global PROCESSES
    PROCESSES.append(report_process)

    print_process_output("Output of result list",
                         *report_process.communicate())

    print("Getting report list for {} {}".format(
        run_name,
        "is done" if report_process.returncode == 0 else "failed"))

    return report_process.returncode


@return_duration
def delete_run(run_name, server_url):
    print("Deleting run {} is started".format(run_name))

    delete_process = subprocess.Popen([
        'CodeChecker', 'cmd', 'del',
        '--url', server_url,
        '-n', run_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)

    global PROCESSES
    PROCESSES.append(delete_process)

    print_process_output("Output of run deletion",
                         *delete_process.communicate())

    print("Deleting run {} {}".format(
        run_name,
        "is done" if delete_process.returncode == 0 else "failed"))

    return delete_process.returncode


def simulate_user(report_dirs, server_url, stat, beta, rounds):
    user = UserSimulator(stat, beta)
    run_name = 'performance_test_' + str(user.get_id())

    for report_dir in report_dirs:
        user.add_action(
            'Storage',
            store_report_dir,
            (report_dir, run_name, server_url))

        user.add_action(
            'Comparison',
            local_compare,
            (report_dir, run_name, server_url))

        user.add_action(
            'Reports',
            get_reports,
            (run_name, server_url))

    user.add_action(
        'Delete',
        delete_run,
        (run_name, server_url))

    while rounds != 0 and not FINISH:
        rounds -= 1
        user.play()

    os.kill(os.getpid(), signal.SIGUSR1)


def main():
    global VERBOSE

    args = parse_arguments()

    VERBOSE = args.verbose

    stat = StatManager()

    def finish_test(signum, frame):
        print('-----> Performance test stops. '
              'Please wait for stopping all subprocesses. <-----')

        global FINISH
        FINISH = True

        global PROCESSES
        for proc in PROCESSES:
            try:
                proc.terminate()
            except OSError:
                pass

        stat.print_stats(args.output)
        print("Performance test has timed out or killed.")
        sys.exit(128 + signum)

    signal.signal(signal.SIGINT, finish_test)

    print(os.environ['PATH'])
    threads = [threading.Thread(
        target=simulate_user,
        args=(args.input, args.product_url, stat, args.beta, args.rounds))
        for _ in range(args.users)]

    for t in threads:
        t.start()

    if args.timeout > 0:
        threading.Timer(args.timeout,
                        lambda: os.kill(os.getpid(), signal.SIGINT)).start()

    # This command hangs the process until a signal is emitted. This signal may
    # come either from the user by hitting Ctrl-C or by the simulate_user()
    # function when it is completed.
    signal.pause()

    for t in threads:
        t.join()

    stat.print_stats(args.output)


if __name__ == '__main__':
    main()
